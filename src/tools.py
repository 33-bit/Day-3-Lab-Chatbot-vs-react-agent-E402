"""Security-hardened tools cho trợ lý tra cứu đơn hàng và đổi/trả.

Các tool public luôn trả chuỗi JSON để ReAct Agent dùng làm Observation. Mọi
thao tác thay đổi trạng thái cần xác thực đơn, xác nhận người dùng do application
cấp, và idempotency key. Yêu cầu được lưu bằng SQLite để tồn tại qua restart và
được bảo vệ bởi transaction trước các lời gọi đồng thời.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


LOGGER = logging.getLogger(__name__)

RETURN_WINDOW_DAYS = 15
REQUEST_PENDING = "pending_review"
AUTH_SESSION_TTL_SECONDS = 5 * 60
CONFIRMATION_TTL_SECONDS = 2 * 60

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_DB_PATH = Path(
    os.getenv("TOOLS_DB_PATH", str(PROJECT_ROOT / "data" / "tools_state.sqlite3"))
)
DATA_FILE_PATH = Path(
    os.getenv("TOOLS_DATA_PATH", str(PROJECT_ROOT / "data" / "sample_orders.json"))
)
ENABLE_FAILURE_SIMULATION = (
    os.getenv("TOOLS_ENABLE_FAILURE_SIMULATION", "0").strip() == "1"
)

MAX_ORDER_ID_LENGTH = 32
MAX_ITEM_ID_LENGTH = 64
MAX_CONTACT_LENGTH = 254
MAX_REASON_LENGTH = 500
MAX_EVIDENCE_LENGTH = 512
MAX_VARIANT_LENGTH = 100
MAX_TOKEN_LENGTH = 256
MAX_IDEMPOTENCY_KEY_LENGTH = 64


def _load_sample_data(
    data_file_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Đọc và kiểm tra dataset 10 đơn hàng từ JSON."""
    try:
        with data_file_path.open("r", encoding="utf-8") as data_file:
            payload = json.load(data_file)
    except (OSError, json.JSONDecodeError) as exception:
        raise RuntimeError(
            f"Không thể đọc dữ liệu đơn hàng tại '{data_file_path}'."
        ) from exception

    raw_orders = payload.get("orders")
    inventory = payload.get("inventory")
    if not isinstance(raw_orders, list) or len(raw_orders) != 10:
        raise RuntimeError("Dataset mẫu phải chứa đúng 10 đơn hàng.")
    if not isinstance(inventory, dict):
        raise RuntimeError("Dataset mẫu thiếu inventory hợp lệ.")

    orders: dict[str, dict[str, Any]] = {}
    for order in raw_orders:
        if not isinstance(order, dict) or not isinstance(order.get("order_id"), str):
            raise RuntimeError("Mỗi order phải là object và có order_id.")
        order_id = order["order_id"]
        if order_id in orders:
            raise RuntimeError(f"order_id bị trùng trong dataset: {order_id}")
        if not isinstance(order.get("test_scenario"), str):
            raise RuntimeError(f"Order {order_id} thiếu test_scenario.")
        items = order.get("items")
        if not isinstance(items, list) or not items:
            raise RuntimeError(f"Order {order_id} phải có ít nhất một item.")

        for item in items:
            item_id = item.get("item_id")
            inventory_item = inventory.get(item_id)
            if not isinstance(inventory_item, dict):
                raise RuntimeError(f"Inventory thiếu item {item_id} của {order_id}.")
            exchange_options = inventory_item.get("exchange_options")
            if not isinstance(exchange_options, dict):
                raise RuntimeError(f"Inventory {item_id} thiếu exchange_options.")
            # Inject reference cấp sản phẩm để giữ tương thích phần nghiệp vụ.
            item["exchange_options"] = exchange_options

        orders[order_id] = order

    return orders, inventory


# Dữ liệu giả lập deterministic được tách khỏi code để dễ đọc và mở rộng.
MOCK_ORDERS, MOCK_INVENTORY = _load_sample_data(DATA_FILE_PATH)


_SECURITY_LOCK = threading.RLock()
_DATABASE_LOCK = threading.RLock()
_AUTH_SESSIONS: dict[str, dict[str, Any]] = {}
_CONFIRMATION_SESSIONS: dict[str, dict[str, Any]] = {}
_RATE_LIMIT_BUCKETS: dict[str, list[float]] = {}


class ToolError(Exception):
    """Lỗi có mã ổn định để chuyển thành Observation."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def _result(success: bool, **payload: Any) -> str:
    """Serialize kết quả thành JSON Unicode."""
    return json.dumps(
        {"success": success, **payload},
        ensure_ascii=False,
        indent=2,
    )


def _error(error_code: str, message: str) -> str:
    """Tạo Observation lỗi thống nhất."""
    return _result(False, error_code=error_code, message=message)


def _required_text(
    value: Any,
    field_name: str,
    *,
    minimum_length: int = 1,
    maximum_length: int,
) -> str:
    """Validate type và giới hạn độ dài của input text."""
    if not isinstance(value, str):
        raise ToolError("VALIDATION_ERROR", f"'{field_name}' phải là chuỗi.")

    normalized = value.strip()
    if len(normalized) < minimum_length:
        raise ToolError(
            "VALIDATION_ERROR",
            f"'{field_name}' phải có ít nhất {minimum_length} ký tự.",
        )
    if len(normalized) > maximum_length:
        raise ToolError(
            "VALIDATION_ERROR",
            f"'{field_name}' không được vượt quá {maximum_length} ký tự.",
        )
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", normalized):
        raise ToolError(
            "VALIDATION_ERROR",
            f"'{field_name}' chứa ký tự điều khiển không hợp lệ.",
        )
    return normalized


def _normalize_order_id(value: Any) -> str:
    order_id = _required_text(
        value,
        "order_id",
        maximum_length=MAX_ORDER_ID_LENGTH,
    ).upper()
    if re.fullmatch(r"DH\d{3,}", order_id) is None:
        raise ToolError(
            "VALIDATION_ERROR",
            "Mã đơn phải có dạng 'DH' và ít nhất 3 chữ số, ví dụ DH001.",
        )
    return order_id


def _normalize_item_id(value: Any) -> str:
    item_id = _required_text(
        value,
        "item_id",
        maximum_length=MAX_ITEM_ID_LENGTH,
    ).upper()
    if re.fullmatch(r"SP-[A-Z0-9-]+", item_id) is None:
        raise ToolError(
            "VALIDATION_ERROR",
            "item_id phải bắt đầu bằng 'SP-' và chỉ chứa chữ, số hoặc dấu '-'.",
        )
    return item_id


def _normalize_contact(value: Any) -> str:
    contact = _required_text(
        value,
        "customer_contact",
        maximum_length=MAX_CONTACT_LENGTH,
    )
    if "@" in contact:
        normalized = contact.lower()
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized) is None:
            raise ToolError(
                "VALIDATION_ERROR",
                "customer_contact phải là email hoặc số điện thoại hợp lệ.",
            )
        return normalized

    normalized = re.sub(r"[\s.-]", "", contact)
    if re.fullmatch(r"\+?\d{9,12}", normalized) is None:
        raise ToolError(
            "VALIDATION_ERROR",
            "customer_contact phải là email hoặc số điện thoại hợp lệ.",
        )
    return normalized


def _normalize_quantity(value: Any) -> int:
    if isinstance(value, bool):
        raise ToolError("VALIDATION_ERROR", "quantity phải là số nguyên dương.")
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int) or value <= 0 or value > 100:
        raise ToolError(
            "VALIDATION_ERROR",
            "quantity phải là số nguyên từ 1 đến 100.",
        )
    return value


def _normalize_reason(value: Any) -> str:
    if not isinstance(value, str) or len(value.strip()) < 5:
        raise ToolError(
            "MISSING_EVIDENCE",
            "Cần cung cấp lý do đổi/trả có ít nhất 5 ký tự.",
        )
    reason = _required_text(value, "reason", maximum_length=MAX_REASON_LENGTH)
    return re.sub(r"\s+", " ", reason)


def _normalize_evidence(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolError(
            "MISSING_EVIDENCE",
            "Cần cung cấp URL hoặc mã tham chiếu ảnh/video bằng chứng.",
        )
    evidence = _required_text(
        value,
        "evidence",
        maximum_length=MAX_EVIDENCE_LENGTH,
    )
    if evidence.startswith("evidence://"):
        if re.fullmatch(r"evidence://[A-Za-z0-9._/-]+", evidence) is None:
            raise ToolError(
                "INVALID_EVIDENCE",
                "Mã evidence:// chứa ký tự không hợp lệ.",
            )
        if ".." in evidence:
            raise ToolError("INVALID_EVIDENCE", "Đường dẫn bằng chứng không hợp lệ.")
        return evidence

    parsed = urlparse(evidence)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username:
        raise ToolError(
            "INVALID_EVIDENCE",
            "Bằng chứng phải là URL HTTPS hoặc mã evidence:// hợp lệ.",
        )
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
    ):
        raise ToolError(
            "INVALID_EVIDENCE",
            "Không chấp nhận URL bằng chứng trỏ tới địa chỉ nội bộ.",
        )
    return evidence


def _normalize_idempotency_key(value: Any) -> str:
    key = _required_text(
        value,
        "idempotency_key",
        minimum_length=16,
        maximum_length=MAX_IDEMPOTENCY_KEY_LENGTH,
    )
    if re.fullmatch(r"[A-Za-z0-9._:-]+", key) is None:
        raise ToolError(
            "VALIDATION_ERROR",
            "idempotency_key chỉ được chứa chữ, số, '.', '_', ':' hoặc '-'.",
        )
    return key


def _prune_security_state(now: float) -> None:
    """Xóa token và rate-limit bucket đã hết hạn; caller giữ lock."""
    expired_auth = [
        token
        for token, session in _AUTH_SESSIONS.items()
        if session["expires_at"] <= now
    ]
    for token in expired_auth:
        _AUTH_SESSIONS.pop(token, None)

    expired_confirmations = [
        token
        for token, session in _CONFIRMATION_SESSIONS.items()
        if session["expires_at"] <= now or session["used"]
    ]
    for token in expired_confirmations:
        _CONFIRMATION_SESSIONS.pop(token, None)


def _check_rate_limit(key: str, limit: int, period_seconds: int) -> None:
    """Rate limit đơn giản theo tiến trình cho demo và pitching."""
    now = time.monotonic()
    with _SECURITY_LOCK:
        _prune_security_state(now)
        threshold = now - period_seconds
        timestamps = [
            timestamp
            for timestamp in _RATE_LIMIT_BUCKETS.get(key, [])
            if timestamp > threshold
        ]
        if len(timestamps) >= limit:
            raise ToolError(
                "RATE_LIMITED",
                "Có quá nhiều yêu cầu; vui lòng chờ rồi thử lại.",
            )
        timestamps.append(now)
        _RATE_LIMIT_BUCKETS[key] = timestamps


def _lookup_order(order_id: str) -> dict[str, Any] | None:
    """Giả lập backend; fault injection chỉ bật rõ ràng trong test mode."""
    if ENABLE_FAILURE_SIMULATION:
        if order_id == "DH9998":
            raise TimeoutError("Order database timed out")
        if order_id == "DH9999":
            raise ConnectionError("Order database is unavailable")
    return MOCK_ORDERS.get(order_id)


def _find_item(order: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in order["items"] if item["item_id"] == item_id),
        None,
    )


def _connect_database() -> sqlite3.Connection:
    """Mở SQLite database và bảo đảm schema tồn tại."""
    STATE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(STATE_DB_PATH), timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS after_sales_requests (
            request_id TEXT PRIMARY KEY,
            request_type TEXT NOT NULL,
            order_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            evidence TEXT,
            preferred_item TEXT,
            status TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_request_per_item
        ON after_sales_requests(order_id, item_id)
        WHERE status IN ('pending_review', 'approved')
        """
    )
    return connection


def _request_payload(
    *,
    request_type: str,
    order_id: str,
    item_id: str,
    reason: str,
    quantity: int,
    evidence: str | None,
    preferred_item: str | None,
    idempotency_key: str,
) -> dict[str, Any]:
    return {
        "request_type": request_type,
        "order_id": order_id,
        "item_id": item_id,
        "reason": reason,
        "quantity": quantity,
        "evidence": evidence,
        "preferred_item": preferred_item,
        "idempotency_key": idempotency_key,
    }


def _same_request(row: sqlite3.Row, payload: dict[str, Any]) -> bool:
    fields = (
        "request_type",
        "order_id",
        "item_id",
        "reason",
        "quantity",
        "evidence",
        "preferred_item",
        "idempotency_key",
    )
    return all(row[field] == payload[field] for field in fields)


def _public_request(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """Không echo reason/evidence/idempotency key vào LLM Observation."""
    request = dict(row)
    safe_fields = (
        "request_id",
        "request_type",
        "order_id",
        "item_id",
        "quantity",
        "preferred_item",
        "status",
        "created_at",
    )
    return {field: request.get(field) for field in safe_fields}


def _get_idempotent_request(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Trả request cũ khi retry đúng payload; chặn tái dùng key khác payload."""
    with _DATABASE_LOCK:
        connection = _connect_database()
        try:
            row = connection.execute(
                "SELECT * FROM after_sales_requests WHERE idempotency_key = ?",
                (payload["idempotency_key"],),
            ).fetchone()
            if row is None:
                return None
            if not _same_request(row, payload):
                raise ToolError(
                    "IDEMPOTENCY_CONFLICT",
                    "idempotency_key đã được dùng cho một yêu cầu khác.",
                )
            return _public_request(row)
        finally:
            connection.close()


def _insert_request(
    payload: dict[str, Any],
    *,
    available_stock: int | None = None,
) -> tuple[dict[str, Any], bool]:
    """Insert nguyên tử; trả (request, replayed)."""
    with _DATABASE_LOCK:
        connection = _connect_database()
        try:
            connection.execute("BEGIN IMMEDIATE")

            idempotent_row = connection.execute(
                "SELECT * FROM after_sales_requests WHERE idempotency_key = ?",
                (payload["idempotency_key"],),
            ).fetchone()
            if idempotent_row is not None:
                if not _same_request(idempotent_row, payload):
                    raise ToolError(
                        "IDEMPOTENCY_CONFLICT",
                        "idempotency_key đã được dùng cho một yêu cầu khác.",
                    )
                connection.commit()
                return _public_request(idempotent_row), True

            active_row = connection.execute(
                """
                SELECT * FROM after_sales_requests
                WHERE order_id = ? AND item_id = ?
                  AND status IN ('pending_review', 'approved')
                """,
                (payload["order_id"], payload["item_id"]),
            ).fetchone()
            if active_row is not None:
                error_code = (
                    "DUPLICATE_REQUEST"
                    if active_row["request_type"] == payload["request_type"]
                    else "CONFLICTING_REQUEST"
                )
                raise ToolError(
                    error_code,
                    "Sản phẩm đã có yêu cầu đổi/trả đang được xử lý.",
                )

            if available_stock is not None:
                reserved_row = connection.execute(
                    """
                    SELECT COALESCE(SUM(quantity), 0) AS reserved
                    FROM after_sales_requests
                    WHERE request_type = 'exchange'
                      AND item_id = ? AND preferred_item = ?
                      AND status IN ('pending_review', 'approved')
                    """,
                    (payload["item_id"], payload["preferred_item"]),
                ).fetchone()
                available = available_stock - int(reserved_row["reserved"])
                if available < payload["quantity"]:
                    raise ToolError(
                        "OUT_OF_STOCK",
                        "Biến thể muốn đổi hiện không đủ tồn kho khả dụng.",
                    )

            request_id_prefix = "YCT" if payload["request_type"] == "return" else "YCD"
            record = {
                **payload,
                "request_id": f"{request_id_prefix}-{uuid.uuid4().hex[:12].upper()}",
                "status": REQUEST_PENDING,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            connection.execute(
                """
                INSERT INTO after_sales_requests (
                    request_id, request_type, order_id, item_id, reason,
                    quantity, evidence, preferred_item, status,
                    idempotency_key, created_at
                ) VALUES (
                    :request_id, :request_type, :order_id, :item_id, :reason,
                    :quantity, :evidence, :preferred_item, :status,
                    :idempotency_key, :created_at
                )
                """,
                record,
            )
            connection.commit()
            return _public_request(record), False
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _reserved_quantity(item_id: str, preferred_item: str) -> int:
    with _DATABASE_LOCK:
        connection = _connect_database()
        try:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(quantity), 0) AS reserved
                FROM after_sales_requests
                WHERE request_type = 'exchange'
                  AND item_id = ? AND preferred_item = ?
                  AND status IN ('pending_review', 'approved')
                """,
                (item_id, preferred_item),
            ).fetchone()
            return int(row["reserved"])
        finally:
            connection.close()


def _safe_order_data(order: dict[str, Any]) -> dict[str, Any]:
    """Redact PII và chỉ công bố trạng thái kho dạng available boolean."""
    safe_items = []
    for item in order["items"]:
        options = []
        for option_name, option in item["exchange_options"].items():
            reserved = _reserved_quantity(item["item_id"], option_name)
            options.append(
                {
                    "name": option_name,
                    "available": option["stock"] - reserved > 0,
                    "same_price": option["unit_price"] == item["unit_price"],
                }
            )
        safe_items.append(
            {
                "item_id": item["item_id"],
                "name": item["name"],
                "variant": item["variant"],
                "quantity": item["quantity"],
                "return_eligible": item["return_eligible"],
                "exchange_eligible": item["exchange_eligible"],
                "exchange_options": options,
            }
        )
    return {
        "order_id": order["order_id"],
        "status": order["status"],
        "status_label": order["status_label"],
        "days_since_delivery": order["days_since_delivery"],
        "items": safe_items,
    }


def _issue_auth_token(order_id: str) -> str:
    token = secrets.token_urlsafe(32)
    with _SECURITY_LOCK:
        _AUTH_SESSIONS[token] = {
            "order_id": order_id,
            "expires_at": time.monotonic() + AUTH_SESSION_TTL_SECONDS,
        }
    return token


def _validate_auth_token(value: Any, order_id: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TOKEN_LENGTH:
        raise ToolError(
            "AUTHORIZATION_REQUIRED",
            "Cần tra cứu và xác thực lại đơn hàng trước khi đổi/trả.",
        )
    token = value.strip()
    now = time.monotonic()
    with _SECURITY_LOCK:
        _prune_security_state(now)
        session = _AUTH_SESSIONS.get(token)
        if session is None or not hmac.compare_digest(session["order_id"], order_id):
            raise ToolError(
                "AUTHORIZATION_REQUIRED",
                "Cần tra cứu và xác thực lại đơn hàng trước khi đổi/trả.",
            )
    return token


def issue_confirmation_token(
    auth_token: str,
    order_id: str,
    item_id: str,
    request_type: str,
) -> str:
    """Application-only: cấp token sau khi user xác nhận rõ thao tác.

    Hàm này không nằm trong ``AVAILABLE_TOOLS`` nên model không được tự gọi.
    Role 4 chỉ gọi sau khi giao diện/ứng dụng ghi nhận lời xác nhận của user.
    """
    try:
        normalized_order_id = _normalize_order_id(order_id)
        normalized_item_id = _normalize_item_id(item_id)
        normalized_request_type = _required_text(
            request_type,
            "request_type",
            maximum_length=16,
        ).lower()
        if normalized_request_type not in {"return", "exchange"}:
            raise ToolError(
                "VALIDATION_ERROR",
                "request_type phải là 'return' hoặc 'exchange'.",
            )
        normalized_auth_token = _validate_auth_token(
            auth_token,
            normalized_order_id,
        )
        _check_rate_limit(f"confirm:{normalized_auth_token}", 10, 60)

        confirmation_token = secrets.token_urlsafe(32)
        with _SECURITY_LOCK:
            _CONFIRMATION_SESSIONS[confirmation_token] = {
                "auth_token": normalized_auth_token,
                "order_id": normalized_order_id,
                "item_id": normalized_item_id,
                "request_type": normalized_request_type,
                "expires_at": time.monotonic() + CONFIRMATION_TTL_SECONDS,
                "used": False,
            }
        return _result(
            True,
            confirmation_token=confirmation_token,
            expires_in_seconds=CONFIRMATION_TTL_SECONDS,
        )
    except Exception as exception:
        return _tool_exception_response(exception)


def _validate_confirmation_token(
    value: Any,
    *,
    auth_token: str,
    order_id: str,
    item_id: str,
    request_type: str,
) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TOKEN_LENGTH:
        raise ToolError(
            "CONFIRMATION_REQUIRED",
            "Cần người dùng xác nhận lại thao tác đổi/trả.",
        )
    token = value.strip()
    now = time.monotonic()
    with _SECURITY_LOCK:
        _prune_security_state(now)
        session = _CONFIRMATION_SESSIONS.get(token)
        expected = (auth_token, order_id, item_id, request_type)
        actual = None
        if session is not None:
            actual = (
                session["auth_token"],
                session["order_id"],
                session["item_id"],
                session["request_type"],
            )
        if session is None or session["used"] or actual != expected:
            raise ToolError(
                "CONFIRMATION_REQUIRED",
                "Cần người dùng xác nhận lại thao tác đổi/trả.",
            )
    return token


def _consume_confirmation_token(token: str) -> None:
    with _SECURITY_LOCK:
        session = _CONFIRMATION_SESSIONS.get(token)
        if session is not None:
            session["used"] = True


def _validate_after_sales_order(order: dict[str, Any], request_type: str) -> None:
    if order["status"] != "delivered":
        raise ToolError(
            "INVALID_ORDER_STATUS",
            "Chỉ có thể đổi/trả sau khi đơn đã giao thành công.",
        )
    if order["days_since_delivery"] > RETURN_WINDOW_DAYS:
        error_code = (
            "EXPIRED_RETURN_WINDOW"
            if request_type == "return"
            else "EXPIRED_EXCHANGE_WINDOW"
        )
        raise ToolError(
            error_code,
            f"Đơn đã quá thời hạn đổi/trả {RETURN_WINDOW_DAYS} ngày.",
        )


def _tool_exception_response(exception: Exception) -> str:
    if isinstance(exception, ToolError):
        return _error(exception.error_code, exception.message)
    if isinstance(exception, TimeoutError):
        LOGGER.warning("Order backend timeout")
        return _error(
            "TIMEOUT_ERROR",
            "Hệ thống phản hồi quá thời gian; vui lòng thử lại sau.",
        )
    if isinstance(exception, (ConnectionError, sqlite3.Error, OSError)):
        LOGGER.warning("Order backend unavailable: %s", type(exception).__name__)
        return _error(
            "SERVER_ERROR",
            "Hệ thống đơn hàng tạm thời gián đoạn; vui lòng thử lại sau.",
        )
    LOGGER.exception("Unexpected tool failure")
    return _error(
        "SERVER_ERROR",
        "Hệ thống đơn hàng tạm thời gián đoạn; vui lòng thử lại sau.",
    )


def _idempotent_success(request: dict[str, Any]) -> str:
    return _result(
        True,
        message="Yêu cầu đã được ghi nhận trước đó; không tạo bản ghi trùng.",
        replayed=True,
        request=request,
    )


def search_order(order_id: str = "", customer_contact: str = "") -> str:
    """Xác thực và tra cứu một đơn; không làm lộ PII hoặc tồn kho chính xác."""
    try:
        normalized_order_id = _normalize_order_id(order_id)
        normalized_contact = _normalize_contact(customer_contact)
        _check_rate_limit("search:global", 60, 60)
        _check_rate_limit(f"search:{normalized_order_id}", 5, 60)

        order = _lookup_order(normalized_order_id)
        stored_contact = "invalid-contact"
        if order is not None:
            stored_contact = _normalize_contact(order["customer_contact"])

        # Cùng một lỗi cho mã không tồn tại và contact sai để chống enumeration.
        if order is None or not hmac.compare_digest(normalized_contact, stored_contact):
            return _error(
                "ORDER_ACCESS_DENIED",
                "Không thể xác thực đơn hàng với thông tin đã cung cấp.",
            )

        auth_token = _issue_auth_token(normalized_order_id)
        return _result(
            True,
            order=_safe_order_data(order),
            auth_token=auth_token,
            auth_expires_in_seconds=AUTH_SESSION_TTL_SECONDS,
        )
    except Exception as exception:
        return _tool_exception_response(exception)


# Alias giữ tương thích với tên Mốc 2; registry chỉ công bố tên chuẩn số ít.
search_orders = search_order


def create_return_request(
    order_id: str = "",
    item_id: str = "",
    reason: str = "",
    quantity: int = 1,
    evidence: str = "",
    auth_token: str = "",
    confirmation_token: str = "",
    idempotency_key: str = "",
) -> str:
    """Tạo yêu cầu trả hàng đã xác thực, xác nhận và chống gọi trùng."""
    try:
        normalized_order_id = _normalize_order_id(order_id)
        normalized_item_id = _normalize_item_id(item_id)
        normalized_reason = _normalize_reason(reason)
        normalized_quantity = _normalize_quantity(quantity)
        normalized_evidence = _normalize_evidence(evidence)
        normalized_idempotency_key = _normalize_idempotency_key(idempotency_key)
        normalized_auth_token = _validate_auth_token(
            auth_token,
            normalized_order_id,
        )
        _check_rate_limit(f"mutation:{normalized_auth_token}", 20, 60)

        payload = _request_payload(
            request_type="return",
            order_id=normalized_order_id,
            item_id=normalized_item_id,
            reason=normalized_reason,
            quantity=normalized_quantity,
            evidence=normalized_evidence,
            preferred_item=None,
            idempotency_key=normalized_idempotency_key,
        )
        existing = _get_idempotent_request(payload)
        if existing is not None:
            return _idempotent_success(existing)

        normalized_confirmation_token = _validate_confirmation_token(
            confirmation_token,
            auth_token=normalized_auth_token,
            order_id=normalized_order_id,
            item_id=normalized_item_id,
            request_type="return",
        )

        order = _lookup_order(normalized_order_id)
        if order is None:
            raise ToolError(
                "ORDER_ACCESS_DENIED",
                "Không thể xác thực đơn hàng với thông tin đã cung cấp.",
            )
        _validate_after_sales_order(order, "return")

        item = _find_item(order, normalized_item_id)
        if item is None:
            raise ToolError("ITEM_NOT_FOUND", "Sản phẩm không thuộc đơn hàng.")
        if not item["return_eligible"]:
            raise ToolError(
                "INELIGIBLE_ITEM",
                item.get("ineligible_reason", "Sản phẩm không được phép trả."),
            )
        if normalized_quantity > item["quantity"]:
            raise ToolError(
                "INVALID_QUANTITY",
                "Số lượng trả vượt quá số lượng đã mua.",
            )

        request, replayed = _insert_request(payload)
        if not replayed:
            _consume_confirmation_token(normalized_confirmation_token)
        return _result(
            True,
            message="Đã tạo yêu cầu trả hàng; chưa thực hiện hoàn tiền.",
            replayed=replayed,
            return_request=request,
        )
    except Exception as exception:
        return _tool_exception_response(exception)


def create_exchange_request(
    order_id: str = "",
    item_id: str = "",
    reason: str = "",
    quantity: int = 1,
    preferred_item: str = "",
    auth_token: str = "",
    confirmation_token: str = "",
    idempotency_key: str = "",
) -> str:
    """Tạo yêu cầu đổi hàng an toàn và giữ tồn kho bằng transaction."""
    try:
        normalized_order_id = _normalize_order_id(order_id)
        normalized_item_id = _normalize_item_id(item_id)
        normalized_reason = _normalize_reason(reason)
        normalized_quantity = _normalize_quantity(quantity)
        normalized_preferred_item = _required_text(
            preferred_item,
            "preferred_item",
            maximum_length=MAX_VARIANT_LENGTH,
        )
        normalized_idempotency_key = _normalize_idempotency_key(idempotency_key)
        normalized_auth_token = _validate_auth_token(
            auth_token,
            normalized_order_id,
        )
        _check_rate_limit(f"mutation:{normalized_auth_token}", 20, 60)

        order = _lookup_order(normalized_order_id)
        if order is None:
            raise ToolError(
                "ORDER_ACCESS_DENIED",
                "Không thể xác thực đơn hàng với thông tin đã cung cấp.",
            )
        item = _find_item(order, normalized_item_id)
        if item is None:
            raise ToolError("ITEM_NOT_FOUND", "Sản phẩm không thuộc đơn hàng.")

        _validate_after_sales_order(order, "exchange")
        if not item["exchange_eligible"]:
            raise ToolError(
                "INELIGIBLE_ITEM",
                item.get("ineligible_reason", "Sản phẩm không được phép đổi."),
            )
        if normalized_quantity > item["quantity"]:
            raise ToolError(
                "INVALID_QUANTITY",
                "Số lượng đổi vượt quá số lượng đã mua.",
            )

        matching_option_name = None
        matching_option = None
        for option_name, option in item["exchange_options"].items():
            if option_name.casefold() == normalized_preferred_item.casefold():
                matching_option_name = option_name
                matching_option = option
                break
        if matching_option is None or matching_option_name is None:
            raise ToolError(
                "ITEM_NOT_FOUND",
                "Không tìm thấy biến thể muốn đổi trong danh sách lựa chọn.",
            )

        payload = _request_payload(
            request_type="exchange",
            order_id=normalized_order_id,
            item_id=normalized_item_id,
            reason=normalized_reason,
            quantity=normalized_quantity,
            evidence=None,
            preferred_item=matching_option_name,
            idempotency_key=normalized_idempotency_key,
        )
        existing = _get_idempotent_request(payload)
        if existing is not None:
            return _idempotent_success(existing)

        normalized_confirmation_token = _validate_confirmation_token(
            confirmation_token,
            auth_token=normalized_auth_token,
            order_id=normalized_order_id,
            item_id=normalized_item_id,
            request_type="exchange",
        )
        if matching_option["unit_price"] != item["unit_price"]:
            raise ToolError(
                "PRICE_MISMATCH",
                "Biến thể có giá khác; tool không hỗ trợ thanh toán bù trừ.",
            )

        request, replayed = _insert_request(
            payload,
            available_stock=matching_option["stock"],
        )
        if not replayed:
            _consume_confirmation_token(normalized_confirmation_token)
        return _result(
            True,
            message="Đã tạo yêu cầu đổi hàng và giữ phần tồn kho tương ứng.",
            replayed=replayed,
            exchange_request=request,
        )
    except Exception as exception:
        return _tool_exception_response(exception)


def _reset_state_for_tests() -> None:
    """Test-only helper; không đăng ký cho Agent."""
    with _SECURITY_LOCK:
        _AUTH_SESSIONS.clear()
        _CONFIRMATION_SESSIONS.clear()
        _RATE_LIMIT_BUCKETS.clear()
    for path in (
        STATE_DB_PATH,
        Path(f"{STATE_DB_PATH}-wal"),
        Path(f"{STATE_DB_PATH}-shm"),
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


TOOL_SPECS = {
    "search_order": {
        "description": (
            "Xác thực và tra cứu đơn bằng mã DH cùng email/số điện thoại; "
            "thành công trả auth_token ngắn hạn."
        ),
        "parameters": {
            "order_id": "Mã đơn theo format DH001.",
            "customer_contact": "Email hoặc số điện thoại người đặt.",
        },
        "required": ["order_id", "customer_contact"],
        "side_effect": "read_only_and_issue_short_lived_auth_token",
    },
    "create_return_request": {
        "description": (
            "Tạo yêu cầu trả đã xác thực và được user xác nhận; không tự động "
            "hoàn tiền."
        ),
        "parameters": {
            "order_id": "Mã đơn DH...",
            "item_id": "SKU SP-...",
            "reason": "Lý do từ 5 đến 500 ký tự.",
            "quantity": "Số lượng nguyên dương.",
            "evidence": "URL HTTPS hoặc mã evidence://.",
        },
        "required": ["order_id", "item_id", "reason", "quantity", "evidence"],
        "executor_injected_parameters": [
            "auth_token",
            "confirmation_token",
            "idempotency_key",
        ],
        "side_effect": "persist_return_request",
    },
    "create_exchange_request": {
        "description": (
            "Tạo yêu cầu đổi đã xác thực/xác nhận, chặn xung đột và giữ tồn "
            "kho bằng transaction."
        ),
        "parameters": {
            "order_id": "Mã đơn DH...",
            "item_id": "SKU SP-...",
            "reason": "Lý do từ 5 đến 500 ký tự.",
            "quantity": "Số lượng nguyên dương.",
            "preferred_item": "Tên biến thể đúng từ kết quả search_order.",
        },
        "required": [
            "order_id",
            "item_id",
            "reason",
            "quantity",
            "preferred_item",
        ],
        "executor_injected_parameters": [
            "auth_token",
            "confirmation_token",
            "idempotency_key",
        ],
        "side_effect": "persist_exchange_request_and_reserve_inventory",
    },
}


AVAILABLE_TOOLS = {
    "search_order": search_order,
    "create_return_request": create_return_request,
    "create_exchange_request": create_exchange_request,
}


__all__ = [
    "AVAILABLE_TOOLS",
    "TOOL_SPECS",
    "search_order",
    "search_orders",
    "create_return_request",
    "create_exchange_request",
    "issue_confirmation_token",
]
