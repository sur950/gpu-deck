/**
 * Sign-in form — one seeded admin, username + password only.
 * No SSO, LDAP, sign-up or forgot-password: a single card.
 */

"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useLogin } from "@/hooks/use-auth";
import { LoadingSpinner } from "@/components/shared/LoadingSkeleton";
import { PasswordInput } from "@/components/shared/PasswordInput";
import { APP_NAME } from "@/utils/brand-env";
import { isMockMode } from "@/lib/mocks";
import { toast } from "sonner";
import { ArrowBigUpDash, FlaskConical } from "lucide-react";

/**
 * Where to go after signing in: the `from` page when it is a same-origin path,
 * else the dashboard. Browsers treat `\` as `/` and strip tabs/newlines, so
 * `/\evil.com` or `/<TAB>/evil.com` would become the protocol-relative
 * `//evil.com`; the candidate is resolved against a sentinel origin and only
 * kept when it stays on that origin.
 */
export function safeRedirect(from: string | null): string {
  const fallback = "/dashboard";
  if (!from || !from.startsWith("/") || /[\\\u0000-\u001f\u007f]/.test(from)) return fallback;
  let url: URL;
  try {
    url = new URL(from, "http://gpu-deck.invalid");
  } catch {
    return fallback;
  }
  if (url.origin !== "http://gpu-deck.invalid") return fallback;
  const path = `${url.pathname}${url.search}${url.hash}`;
  if (path === "/login" || path.startsWith("/login/") || path.startsWith("/login?")) return fallback;
  return path;
}

export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const loginMutation = useLogin();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [capsLock, setCapsLock] = useState(false);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!username.trim()) { setError("Please enter your username"); return; }
    if (!password) { setError("Please enter your password"); return; }

    setIsLoading(true);
    try {
      await loginMutation.mutateAsync({ username: username.trim(), password });
      toast.success("Signed in");
      router.push(safeRedirect(searchParams.get("from")));
      router.refresh();
    } catch (err) {
      // useLogin() throws on failure and the API client keeps the backend's
      // 401 message, so the real reason (wrong password vs. the lockout
      // notice with the remaining minutes) reaches the user.
      setError(err instanceof Error ? err.message : "Invalid username or password");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <Card className="w-full max-w-md shadow-premium-lg">
      <CardHeader>
        <CardTitle>Sign in to {APP_NAME}</CardTitle>
        <CardDescription>Enter the admin credentials to continue</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleLogin} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="username">Username</Label>
            <Input
              id="username"
              type="text"
              placeholder="admin"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              autoFocus
              disabled={isLoading}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <PasswordInput
              id="password"
              placeholder="Enter your password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyUp={(e) => setCapsLock(e.getModifierState("CapsLock"))}
              onKeyDown={(e) => setCapsLock(e.getModifierState("CapsLock"))}
              onBlur={() => setCapsLock(false)}
              autoComplete="current-password"
              disabled={isLoading}
            />
            {capsLock && (
              <p className="flex items-center gap-1.5 text-xs text-warning-dark dark:text-warning-light" role="status">
                <ArrowBigUpDash className="size-3.5" aria-hidden />
                Caps Lock is on
              </p>
            )}
          </div>
          {error && <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
          <Button type="submit" className="w-full" disabled={isLoading}>
            {isLoading ? <LoadingSpinner className="h-4 w-4" /> : "Sign in"}
          </Button>
        </form>
        {isMockMode() && (
          <p className="mt-4 flex items-start gap-2 rounded-md border border-info/25 bg-info/10 p-3 text-xs text-info-dark dark:text-info-light">
            <FlaskConical className="mt-px size-3.5 shrink-0" aria-hidden />
            <span>
              Mock mode: the API is served from fixtures. Sign in as <span className="font-semibold">admin</span> /{" "}
              <span className="font-semibold">admin-admin-admin</span>.
            </span>
          </p>
        )}
      </CardContent>
    </Card>
  );
}
