import React from "react";
import ReactDOM from "react-dom/client";

import "./styles.css";
import { ThemeProvider } from "./lib/theme.jsx";
import App from "./app/App";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </React.StrictMode>
);
