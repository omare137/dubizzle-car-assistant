import { useState } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, createSession } from "@/lib/api";
import { useSession } from "@/context/SessionContext";
import logo from "@/assets/logo.png";

export const Route = createFileRoute("/")({
  component: Landing,
});

function Landing() {
  const [username, setUsername] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const { setSession } = useSession();
  const navigate = useNavigate();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const s = await createSession(username);
      setSession(s);
      navigate({ to: "/chat" });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 404) {
          setError("Unknown username — try again or continue as guest");
        } else if (err.status === 0) {
          setFatal("Can't reach the assistant right now");
        } else {
          setError(err.detail || "Something went wrong");
        }
      } else {
        setFatal("Can't reach the assistant right now");
      }
    } finally {
      setLoading(false);
    }
  }

  if (fatal) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <div className="max-w-md text-center">
          <h1 className="text-2xl font-semibold text-foreground">{fatal}</h1>
          <p className="mt-2 text-muted-foreground">
            The assistant service isn't responding. Please try again in a moment.
          </p>
          <Button className="mt-6" onClick={() => setFatal(null)}>
            Try again
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
      <div className="w-full max-w-md rounded-3xl border border-border bg-card p-8 shadow-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <img src={logo} alt="" width={72} height={72} className="mb-3" />
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            dubizzle cars assistant
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Find your next used car through a simple conversation.
          </p>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="username">Username (optional)</Label>
            <Input
              id="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Leave empty to continue as guest"
              autoComplete="username"
              disabled={loading}
            />
            {error && <p className="text-sm text-destructive">{error}</p>}
          </div>
          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? "Starting…" : "Start chatting"}
          </Button>
        </form>
      </div>
    </div>
  );
}
