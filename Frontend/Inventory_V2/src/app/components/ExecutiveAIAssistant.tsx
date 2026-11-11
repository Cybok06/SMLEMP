import { useMemo, useState } from 'react';
import { Send, X } from 'lucide-react';

type ChatMessage = {
  role: 'user' | 'assistant';
  content: string;
  meta?: string;
};

type ExecutiveAIAssistantProps = {
  enabled: boolean;
  endpoint?: string;
};

const DEFAULT_QUESTIONS = [
  "What are today's sales?",
  'Compare today vs yesterday',
  'Which agent collected the most this week?',
  'Show low stock items',
  'What sales can we expect tomorrow?',
];

const CYBOK_ICON =
  'https://png.pngtree.com/png-vector/20190214/ourmid/pngtree-customer-support-icon-graphic-design-template-vector-png-image_384609.jpg';

export function ExecutiveAIAssistant({
  enabled,
  endpoint = '/api/ai/chat',
}: ExecutiveAIAssistantProps) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      role: 'assistant',
      content: 'Ask about sales, customers, agents, inventory, or fulfillment. This assistant is read-only and uses safe business summaries only.',
    },
  ]);

  const conversation = useMemo(
    () => messages.map(({ role, content }) => ({ role, content })).slice(-8),
    [messages]
  );

  if (!enabled) return null;

  const submit = async (raw: string) => {
    const message = raw.trim();
    if (!message || loading) return;

    setOpen(true);
    setError('');
    setMessages((current) => [...current, { role: 'user', content: message }]);
    setInput('');
    setLoading(true);
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 90000);

    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        credentials: 'same-origin',
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({
          message,
          conversation,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data?.ok || !data?.answer) {
        throw new Error(
          data?.error ||
            data?.message ||
            (response.status === 403
              ? 'You are not allowed to use CYBOK.'
              : response.status === 429
                ? 'Too many AI requests. Please try again shortly.'
                : 'CYBOK is unavailable right now.')
        );
      }

      setMessages((current) => [
        ...current,
        {
          role: 'assistant',
          content: data.answer,
          meta: Array.isArray(data.data_used) && data.data_used.length
            ? `Data used: ${data.data_used.join(', ')}`
            : undefined,
        },
      ]);
    } catch (submitError) {
      if (submitError instanceof DOMException && submitError.name === 'AbortError') {
        setError('CYBOK request timed out. Please try again.');
      } else {
        setError(submitError instanceof Error ? submitError.message : 'CYBOK request failed.');
      }
    } finally {
      window.clearTimeout(timeoutId);
      setLoading(false);
    }
  };

  return (
    <div className="fixed bottom-6 right-6 z-[80]">
      {open && (
        <div className="mb-3 flex h-[min(620px,74vh)] w-[min(392px,calc(100vw-2rem))] flex-col overflow-hidden rounded-[24px] border border-slate-200 bg-white shadow-[0_32px_60px_rgba(15,23,42,0.24)]">
          <div className="flex items-center justify-between border-b border-white/10 bg-[linear-gradient(135deg,#0f172a,#1d4ed8_62%,#0f766e)] px-4 py-4 text-white">
            <div className="flex items-center gap-3">
              <img
                src={CYBOK_ICON}
                alt="CYBOK"
                className="h-7 w-7 rounded-full bg-white object-cover shadow-[0_8px_18px_rgba(15,23,42,0.2)]"
              />
              <div className="text-[0.98rem] font-extrabold tracking-[0.04em]">CYBOK</div>
            </div>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded-xl bg-white/10 p-2 transition hover:bg-white/20"
              aria-label="Close CYBOK"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="flex flex-wrap gap-2 border-b border-slate-200 bg-[linear-gradient(180deg,#f8fafc_0%,#f3f8ff_100%)] px-4 py-3">
            {DEFAULT_QUESTIONS.map((question) => (
              <button
                key={question}
                type="button"
                onClick={() => submit(question)}
                className="rounded-full border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 transition hover:-translate-y-0.5 hover:border-blue-200 hover:bg-slate-50"
              >
                {question}
              </button>
            ))}
          </div>

          <div className="flex-1 space-y-3 overflow-y-auto bg-[linear-gradient(180deg,#f9fbff_0%,#eef6ff_100%)] px-4 py-4">
            {messages.map((item, index) => (
              <div
                key={`${item.role}-${index}`}
                className={`max-w-[88%] whitespace-pre-wrap rounded-[18px] px-4 py-3 text-sm leading-6 transition-all duration-200 ${
                  item.role === 'user'
                    ? 'ml-auto rounded-br-md bg-[linear-gradient(135deg,#2563eb,#0f766e)] text-white shadow-[0_10px_20px_rgba(37,99,235,0.18)]'
                    : 'rounded-bl-md border border-slate-200 bg-white text-slate-900 shadow-[0_10px_22px_rgba(15,23,42,0.06)]'
                }`}
              >
                <div>{item.content}</div>
                {item.meta && <div className="mt-2 text-[11px] text-slate-500">{item.meta}</div>}
              </div>
            ))}
            {loading && (
              <div className="max-w-[88%] rounded-[18px] rounded-bl-md border border-slate-200 bg-white px-4 py-3 shadow-[0_10px_22px_rgba(15,23,42,0.06)]">
                <div className="inline-flex items-center gap-1.5">
                  <span className="h-2 w-2 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.2s]" />
                  <span className="h-2 w-2 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.1s]" />
                  <span className="h-2 w-2 animate-bounce rounded-full bg-slate-400" />
                </div>
              </div>
            )}
          </div>

          <div className="border-t border-slate-200 bg-white px-4 py-4">
            <div className="flex items-end gap-3">
              <textarea
                rows={2}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    void submit(input);
                  }
                }}
                disabled={loading}
                maxLength={1000}
                placeholder="Ask a business question..."
                className="min-h-[48px] flex-1 rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none transition focus:border-blue-300 focus:ring-4 focus:ring-blue-100 disabled:cursor-not-allowed disabled:opacity-60"
              />
              <button
                type="button"
                onClick={() => void submit(input)}
                disabled={loading || !input.trim()}
                className="inline-flex h-12 items-center justify-center rounded-2xl bg-slate-950 px-4 text-white transition hover:-translate-y-0.5 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <Send className="h-4 w-4" />
              </button>
            </div>
            <div className={`mt-2 min-h-[18px] text-xs ${error ? 'text-red-600' : 'text-slate-500'}`}>
              {error}
            </div>
          </div>
        </div>
      )}

      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="inline-flex h-[68px] w-[68px] items-center justify-center overflow-hidden rounded-full bg-[linear-gradient(145deg,#ffffff,#e8f1ff)] text-white shadow-[0_18px_38px_rgba(15,23,42,0.22)] transition hover:-translate-y-0.5 hover:scale-[1.03] hover:saturate-105"
        aria-label="Open CYBOK"
      >
        <img src={CYBOK_ICON} alt="CYBOK" className="h-full w-full object-cover" />
      </button>
    </div>
  );
}
