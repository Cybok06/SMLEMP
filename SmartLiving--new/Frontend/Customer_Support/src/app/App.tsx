import { Layout } from "./components/Layout";
import { SupportIdentityProvider } from "./components/SupportIdentityContext";
import { ThemeProvider } from "./components/ThemeContext";

export default function App() {
  return (
    <ThemeProvider>
      <SupportIdentityProvider>
        <div className="size-full" style={{ fontFamily: "var(--font-family-body)" }}>
          <Layout onLogout={() => { window.location.href = "/logout"; }} />
        </div>
      </SupportIdentityProvider>
    </ThemeProvider>
  );
}
