# Order Agent UI design specification

## Product and audience

Order Agent is a working interface for the Day 3 “Chatbot vs ReAct Agent” lab. It serves a customer who wants calm help with an order and a learner who needs to understand the difference between a baseline chatbot and a tool-using agent. The product must feel credible as customer support while exposing enough system state to make the lab objective visible. It must not become a dense admin dashboard or a raw developer console wrapped around a chat box.

## Core experience

The primary screen is a desktop conversation workspace. A restrained left rail contains the Order Agent identity, a new-conversation action, real prompt examples, and an explicit mode switch between Baseline Chatbot and ReAct Agent. The central column contains the conversation, first-run guidance, suggestions sourced from repository test cases, and a persistent composer. A narrow right rail explains the execution mode, selected provider and model, tool availability, and trace events.

Baseline Chatbot is the live path. Submitting a message calls the existing multi-provider adapter through `run_baseline_chatbot()` and displays the response. The interface states that this mode cannot call tools. Provider failures and missing keys appear as calm, actionable notices rather than raw browser errors.

ReAct Agent is present as an honest preview. Users can switch to it and inspect the planned tool registry, but submitting a task must not invent a successful execution. The response explains that the Thought, Action, Observation loop is being completed in milestone 3. The trace rail shows only real system status, never simulated observations.

## Content and layout

Required content includes the `Order Agent` identity, Baseline and ReAct modes, live provider/model state, the current registry tools `search_order`, `create_return_request`, and `create_exchange_request`, prompt examples, and a conversation reset. The primary target is a 1366-1440 px laptop. The layout uses a 252 px left rail, a fluid central conversation, and a 310 px trace rail. The trace becomes a drawer on smaller laptops, while the sidebar becomes an off-canvas menu on mobile. Reading width remains controlled.

## Emotional direction and implementation

The interface should feel warm, capable, quiet, and trustworthy. Its Claude-style reference appears through paper-like neutrals, editorial display type, breathing room, an unhurried composer, and respectful microcopy. It retains independent identity through the OA monogram and a single terracotta accent. Implement with React and Vite on the frontend and FastAPI on the backend. Radix Themes supplies accessible foundations and Phosphor supplies the only icon family. API keys remain server-side. Preserve the existing CLI behavior in `src/app.py`, provide light and dark themes, support reduced motion, and verify loading, empty, error, mobile, baseline, and ReAct preview states.
