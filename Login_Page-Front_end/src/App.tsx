import { FormEvent, useEffect, useState } from "react";

type LoginConfig = {
  action?: string;
  forgotPasswordUrl?: string;
  next?: string;
  messages?: string[];
};

declare global {
  interface Window {
    __SMARTLIVING_LOGIN__?: LoginConfig;
  }
}

const loginConfig = window.__SMARTLIVING_LOGIN__ || {};

function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <div className={compact ? "brand-mark brand-mark--small" : "brand-mark"}>
      <img
        src="/static/login/smartliving-logo.png"
        alt="SMARTLIVING V2 Emporium Plus"
      />
    </div>
  );
}

function UserIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M20 21a8 8 0 0 0-16 0M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10Z" />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7 10V7a5 5 0 0 1 10 0v3M6 10h12a2 2 0 0 1 2 2v8H4v-8a2 2 0 0 1 2-2Z" />
    </svg>
  );
}

function EyeIcon({ hidden }: { hidden: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" />
      <circle cx="12" cy="12" r="2.5" />
      {hidden && <path d="m4 4 16 16" />}
    </svg>
  );
}

function ArrowIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 12h14M14 7l5 5-5 5" />
    </svg>
  );
}

function ShieldIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3 5 6v5c0 4.6 2.8 8 7 10 4.2-2 7-5.4 7-10V6l-7-3Z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  );
}

export default function App() {
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [geo, setGeo] = useState({
    lat: "",
    lng: "",
    accuracy: "",
    timestamp: "",
    available: "false",
    reason: "Not captured",
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    setLoading(true);
  }

  useEffect(() => {
    const isLocalhost = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
    if (!(window.isSecureContext || window.location.protocol === "https:" || isLocalhost)) {
      setGeo((current) => ({ ...current, reason: "HTTPS required" }));
      return;
    }
    if (!("geolocation" in navigator)) {
      setGeo((current) => ({ ...current, reason: "Geolocation unsupported" }));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) => setGeo({
        lat: String(position.coords.latitude),
        lng: String(position.coords.longitude),
        accuracy: String(position.coords.accuracy || ""),
        timestamp: String(position.timestamp),
        available: "true",
        reason: "",
      }),
      (error) => setGeo((current) => ({ ...current, reason: error.message || "Permission denied" })),
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 },
    );
  }, []);
  return (
    <main className="app-shell">
      <div className="ambient ambient--one" />
      <div className="ambient ambient--two" />

      <section className="login-card" aria-label="SMARTLIVING V2 Emporium Plus login">
        <aside className="brand-panel">
          <div className="brand-panel__glow" />
          <div className="brand-panel__grid" />
          <div className="brand-panel__top">
            <BrandMark />
          </div>

          <div className="brand-panel__content">
            <span className="secure-pill">
              <ShieldIcon />
              Secure partner portal
            </span>
            <h1>
              Welcome to <span>SMARTLIVING V2 Emporium Plus.</span>
            </h1>
            <p>
              Your secure workspace for customers, payments and daily operations.
            </p>
          </div>

        </aside>

        <div className="form-panel">
          <header className="mobile-brand">
            <BrandMark compact />
          </header>

          <div className="form-wrap">
            <div className="form-heading">
              <p className="eyebrow">Member access</p>
              <h2>Sign in to your account</h2>
              <p>Enter your details below to continue to your dashboard.</p>
            </div>

            {loginConfig.messages?.length ? (
              <div className="status-message" role="status">
                <ShieldIcon />
                <span>{loginConfig.messages.join(" ")}</span>
              </div>
            ) : null}

            <form method="post" action={loginConfig.action || "/login"} onSubmit={handleSubmit}>
              {loginConfig.next ? <input type="hidden" name="next" value={loginConfig.next} /> : null}
              <input type="hidden" name="geo_lat" value={geo.lat} readOnly />
              <input type="hidden" name="geo_lng" value={geo.lng} readOnly />
              <input type="hidden" name="geo_accuracy" value={geo.accuracy} readOnly />
              <input type="hidden" name="geo_ts" value={geo.timestamp} readOnly />
              <input type="hidden" name="geo_available" value={geo.available} readOnly />
              <input type="hidden" name="geo_reason" value={geo.reason} readOnly />
              <label className="field">
                <span>Username</span>
                <span className="input-shell">
                  <span className="input-icon">
                    <UserIcon />
                  </span>
                  <input
                    name="username"
                    type="text"
                    autoComplete="username"
                    placeholder="Enter your username"
                    required
                  />
                </span>
              </label>

              <label className="field">
                <span>Password</span>
                <span className="input-shell">
                  <span className="input-icon">
                    <LockIcon />
                  </span>
                  <input
                    name="password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="current-password"
                    placeholder="Enter your password"
                    required
                  />
                  <button
                    className="visibility-button"
                    type="button"
                    onClick={() => setShowPassword((value) => !value)}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                  >
                    <EyeIcon hidden={showPassword} />
                  </button>
                </span>
              </label>

              <div className="form-options">
                <label className="checkbox">
                  <input type="checkbox" name="remember_me" />
                  <span>Keep me signed in</span>
                </label>
                {loginConfig.forgotPasswordUrl ? <a href={loginConfig.forgotPasswordUrl}>Forgot password?</a> : null}
              </div>

              <button className="submit-button" type="submit" disabled={loading}>
                <span>{loading ? "Signing in..." : "Sign in"}</span>
                <span className="submit-button__icon">
                  {loading ? <span className="spinner" /> : <ArrowIcon />}
                </span>
              </button>
            </form>

          </div>

          <footer className="form-footer">
            <span>© {new Date().getFullYear()} SMARTLIVING V2 Emporium Plus</span>
          </footer>
        </div>
      </section>
    </main>
  );
}
