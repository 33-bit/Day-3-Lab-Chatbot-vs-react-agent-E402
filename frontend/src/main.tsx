import "@fontsource/dm-sans/400.css";
import "@fontsource/dm-sans/500.css";
import "@fontsource/dm-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/newsreader/500.css";
import "@fontsource/newsreader/600.css";
import "@radix-ui/themes/styles.css";
import "./styles.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Theme } from "@radix-ui/themes";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Theme appearance="inherit" accentColor="orange" grayColor="sand" radius="medium" scaling="100%">
      <App />
    </Theme>
  </StrictMode>,
);
