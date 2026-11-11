import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type SupportProfile = {
  user_id: string;
  name: string;
  username: string;
  role: string;
  can_view_activity_logs?: boolean;
  email: string;
  phone: string;
  branch: string;
  location: string;
  employee_id: string;
  joined: string;
  status: string;
  avatar_initials: string;
};

export type SupportLoginStat = {
  last_login: string;
  total_logins: number;
  unique_ips: number;
  unique_devices: number;
};

export type SupportLoginLog = {
  id: string;
  time: string;
  device: string;
  ip: string;
  location: string;
  status: string;
};

type SupportIdentityState = {
  profile: SupportProfile | null;
  loginStats: SupportLoginStat | null;
  loginLogs: SupportLoginLog[];
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
};

const SupportIdentityContext = createContext<SupportIdentityState>({
  profile: null,
  loginStats: null,
  loginLogs: [],
  loading: true,
  error: "",
  refresh: async () => {},
});

export function SupportIdentityProvider({ children }: { children: ReactNode }) {
  const [profile, setProfile] = useState<SupportProfile | null>(null);
  const [loginStats, setLoginStats] = useState<SupportLoginStat | null>(null);
  const [loginLogs, setLoginLogs] = useState<SupportLoginLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/customer-support/me", {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.message || "Unable to load support profile.");
      }
      setProfile(data.profile ?? null);
      setLoginStats(data.login_stats ?? null);
      setLoginLogs(Array.isArray(data.login_logs) ? data.login_logs : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load support profile.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo(
    () => ({ profile, loginStats, loginLogs, loading, error, refresh }),
    [profile, loginStats, loginLogs, loading, error, refresh],
  );

  return (
    <SupportIdentityContext.Provider value={value}>
      {children}
    </SupportIdentityContext.Provider>
  );
}

export function useSupportIdentity() {
  return useContext(SupportIdentityContext);
}
