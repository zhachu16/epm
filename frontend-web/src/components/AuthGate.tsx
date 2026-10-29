/**
 * Where sign-in will go. The alpha is open, so everyone is let through; with Supabase Auth
 * this asks for a session first, and a shared project link opens after sign-in.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
