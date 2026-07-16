import { useEffect, useRef, useState } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import ReactMarkdown from "react-markdown";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, sendChat, endSession, type ApiCar, type BookingPromptData } from "@/lib/api";
import { useSession } from "@/context/SessionContext";
import { CarCard } from "@/components/CarCard";
import { BookingPrompt } from "@/components/BookingPrompt";
import { titleCase } from "@/lib/format";
import { Send, LogOut, MessageSquare } from "lucide-react";
import logo from "@/assets/logo.png";

export const Route = createFileRoute("/chat")({
  component: ChatPage,
});

interface UserMsg {
  id: string;
  role: "user";
  text: string;
}
interface AssistantMsg {
  id: string;
  role: "assistant";
  text: string;
  cars: ApiCar[];
  booking_prompt: BookingPromptData | null;
  error?: boolean;
}
type Msg = UserMsg | AssistantMsg;

function ChatPage() {
  const { session, clearSession } = useSession();
  const navigate = useNavigate();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!session) {
      navigate({ to: "/" });
    }
  }, [session, navigate]);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, sending]);

  if (!session) return null;

  const showWelcome =
    session.returning_user &&
    session.user != null &&
    session.profile_summary != null;

  async function send(text: string) {
    const clean = text.trim();
    if (!clean || sending || !session || sessionExpired) return;
    const userMsg: UserMsg = {
      id: `u-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      role: "user",
      text: clean,
    };
    setMessages((m) => [...m, userMsg]);
    setInput("");
    setSending(true);
    try {
      const res = await sendChat(session.session_id, clean);
      setMessages((m) => [
        ...m,
        {
          id: `a-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          role: "assistant",
          text: res.reply,
          cars: res.cars || [],
          booking_prompt: res.booking_prompt,
        },
      ]);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setSessionExpired(true);
      } else if (err instanceof ApiError && err.status === 502) {
        setMessages((m) => [
          ...m,
          {
            id: `a-${Date.now()}`,
            role: "assistant",
            text: "The assistant is temporarily unavailable — please try again",
            cars: [],
            booking_prompt: null,
            error: true,
          },
        ]);
      } else {
        setMessages((m) => [
          ...m,
          {
            id: `a-${Date.now()}`,
            role: "assistant",
            text: "Something went wrong sending your message. Please try again.",
            cars: [],
            booking_prompt: null,
            error: true,
          },
        ]);
      }
    } finally {
      setSending(false);
      setTimeout(() => textareaRef.current?.focus(), 0);
    }
  }

  function handleFormSubmit(e: React.FormEvent) {
    e.preventDefault();
    send(input);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  function handleBookCar(car: ApiCar) {
    const msg = `I'd like to book a viewing for the ${car.year} ${titleCase(car.make)} ${titleCase(car.model)}`;
    send(msg);
  }

  async function handleEnd() {
    if (session) {
      try {
        await endSession(session.session_id);
      } catch {
        /* ignore */
      }
    }
    clearSession();
    navigate({ to: "/" });
  }

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="flex items-center justify-between border-b border-border bg-card px-4 py-3">
        <div className="flex items-center gap-3">
          <img src={logo} alt="" width={36} height={36} />
          <div>
            <h1 className="text-sm font-semibold leading-tight text-foreground">
              dubizzle cars assistant
            </h1>
            {session.user && (
              <p className="text-xs text-muted-foreground">Signed in as {session.user.name}</p>
            )}
          </div>
        </div>
        <Button variant="ghost" size="sm" onClick={handleEnd} className="gap-2">
          <LogOut className="h-4 w-4" />
          End session
        </Button>
      </header>

      {sessionExpired && (
        <div className="border-b border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <div className="mx-auto flex max-w-3xl items-center justify-between gap-3">
            <span>Your session has expired. Please start a new one.</span>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                clearSession();
                navigate({ to: "/" });
              }}
            >
              Back to login
            </Button>
          </div>
        </div>
      )}

      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-6">
          {showWelcome && (
            <div className="rounded-2xl border border-primary/30 bg-primary/5 px-4 py-3 text-sm text-foreground">
              <span className="font-medium">Welcome back — {session.user!.name}.</span>{" "}
              <span className="text-muted-foreground">{session.profile_summary}</span>
            </div>
          )}

          {messages.length === 0 && (
            <div className="mt-10 flex flex-col items-center gap-3 text-center">
              <div className="rounded-full bg-primary/10 p-4 text-primary">
                <MessageSquare className="h-6 w-6" />
              </div>
              <h2 className="text-lg font-semibold text-foreground">
                Ask me about SUVs, sedans, budgets,
              </h2>
              <p className="max-w-md text-sm text-muted-foreground">
                …or anything in our inventory. I can help you compare cars and book a viewing.
              </p>
            </div>
          )}

          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end">
                <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-[var(--chat-user)] px-4 py-2.5 text-sm text-[var(--chat-user-foreground)] shadow-sm">
                  {m.text}
                </div>
              </div>
            ) : (
              <div key={m.id} className="flex flex-col items-start gap-3">
                <div
                  className={
                    "max-w-full text-sm leading-relaxed " +
                    (m.error
                      ? "rounded-2xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-destructive"
                      : "text-foreground")
                  }
                >
                  <div className="prose prose-sm max-w-none prose-p:my-2 prose-headings:my-3 dark:prose-invert">
                    <ReactMarkdown>{m.text}</ReactMarkdown>
                  </div>
                </div>
                {m.cars.length > 0 && (
                  <div className="-mx-1 flex w-full gap-3 overflow-x-auto px-1 pb-2">
                    {m.cars.map((c) => (
                      <CarCard key={c.id} car={c} onBook={handleBookCar} />
                    ))}
                  </div>
                )}
                {m.booking_prompt && (
                  <div className="w-full">
                    <BookingPrompt data={m.booking_prompt} onRespond={send} />
                  </div>
                )}
              </div>
            ),
          )}

          {sending && (
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground [animation-delay:-0.3s]" />
              <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground [animation-delay:-0.15s]" />
              <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground" />
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-border bg-card px-4 py-3">
        <form onSubmit={handleFormSubmit} className="mx-auto flex max-w-3xl items-end gap-2">
          <Textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Message the assistant…"
            rows={1}
            disabled={sending || sessionExpired}
            className="max-h-40 min-h-11 resize-none"
          />
          <Button
            type="submit"
            size="icon"
            disabled={sending || sessionExpired || !input.trim()}
            aria-label="Send"
          >
            <Send className="h-4 w-4" />
          </Button>
        </form>
      </div>
    </div>
  );
}
