import {
  cloneElement,
  isValidElement,
  useId,
  useRef,
  type ComponentPropsWithRef,
  type KeyboardEvent,
  type ReactNode,
} from "react";

function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-teal-700 text-white hover:bg-teal-800 disabled:bg-teal-700/50",
  secondary:
    "border border-stone-300 bg-white text-stone-800 hover:bg-stone-100 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-100 dark:hover:bg-stone-800",
  ghost: "text-stone-700 hover:bg-stone-200/70 dark:text-stone-300 dark:hover:bg-stone-800",
  danger: "bg-red-700 text-white hover:bg-red-800 disabled:bg-red-700/50",
};

export function Button({
  variant = "primary",
  size = "md",
  className,
  type = "button",
  ...props
}: ComponentPropsWithRef<"button"> & { variant?: Variant; size?: "sm" | "md" }) {
  return (
    <button
      type={type}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:cursor-not-allowed",
        size === "sm" ? "px-2.5 py-1 text-sm" : "px-3.5 py-2 text-sm",
        VARIANTS[variant],
        className,
      )}
      {...props}
    />
  );
}

// Borders at 3:1 or more against the field and the page (WCAG 1.4.11), placeholders at
// 4.5:1 (src/lib/contrast.test.ts).
const FIELD =
  "w-full rounded-md border border-stone-500 bg-white px-3 py-2 text-sm text-stone-900 placeholder:text-stone-500 dark:border-stone-500 dark:bg-stone-900 dark:text-stone-100 dark:placeholder:text-stone-400";

export function Input({ className, ...props }: ComponentPropsWithRef<"input">) {
  return <input className={cx(FIELD, className)} {...props} />;
}

export function Textarea({ className, ...props }: ComponentPropsWithRef<"textarea">) {
  return <textarea className={cx(FIELD, "min-h-24", className)} {...props} />;
}

export function Select({ className, ...props }: ComponentPropsWithRef<"select">) {
  return <select className={cx(FIELD, "pr-8", className)} {...props} />;
}

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  const hintId = useId();
  // The hint stays outside the <label>: the field's accessible name is the label alone, and
  // the hint becomes its description (aria-describedby) when the child is a single control.
  const control =
    hint && isValidElement<{ "aria-describedby"?: string }>(children)
      ? cloneElement(children, {
          "aria-describedby": [children.props["aria-describedby"], hintId].filter(Boolean).join(" "),
        })
      : children;
  return (
    <div className={cx("flex flex-col gap-1 text-sm", className)}>
      <label className="flex flex-col gap-1">
        <span className="font-medium text-stone-700 dark:text-stone-300">{label}</span>
        {control}
      </label>
      {hint && (
        <span id={hintId} className="text-xs text-muted">
          {hint}
        </span>
      )}
    </div>
  );
}

export interface TabItem<T extends string> {
  id: T;
  label: ReactNode;
}

/**
 * Tabs (WAI-ARIA pattern, automatic activation): only the selected tab is in the tab order,
 * the arrow keys move between tabs, Home and End go to the first and last one. The panel
 * holds `children` (the selected tab's content) and is named by its tab.
 */
export function Tabs<T extends string>({
  label,
  tabs,
  value,
  onChange,
  children,
  className,
}: {
  label: string;
  tabs: TabItem<T>[];
  value: T;
  onChange: (value: T) => void;
  children: ReactNode;
  className?: string;
}) {
  const base = useId();
  const refs = useRef(new Map<T, HTMLButtonElement>());
  const tabId = (id: T) => `${base}-tab-${id}`;
  const panelId = (id: T) => `${base}-panel-${id}`;

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const index = tabs.findIndex((t) => t.id === value);
    const last = tabs.length - 1;
    const next = {
      ArrowRight: index === last ? 0 : index + 1,
      ArrowLeft: index === 0 ? last : index - 1,
      Home: 0,
      End: last,
    }[event.key];
    const target = next === undefined ? undefined : tabs[next];
    if (!target) return;
    event.preventDefault();
    onChange(target.id);
    refs.current.get(target.id)?.focus();
  }

  return (
    <div className={className}>
      <div role="tablist" aria-label={label} className="mb-4 flex flex-wrap gap-2">
        {tabs.map((tab) => {
          const active = tab.id === value;
          return (
            <button
              key={tab.id}
              ref={(node) => {
                if (node) refs.current.set(tab.id, node);
                else refs.current.delete(tab.id);
              }}
              type="button"
              role="tab"
              id={tabId(tab.id)}
              aria-selected={active}
              // Only the shown panel is rendered: the others have no element to point to.
              aria-controls={active ? panelId(tab.id) : undefined}
              tabIndex={active ? 0 : -1}
              onClick={() => onChange(tab.id)}
              onKeyDown={onKeyDown}
              className={cx(
                "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm",
                active
                  ? "bg-teal-100 font-medium text-teal-900 dark:bg-teal-900/50 dark:text-teal-100"
                  : "hover:bg-stone-100 dark:hover:bg-stone-800",
              )}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
      <div role="tabpanel" id={panelId(value)} aria-labelledby={tabId(value)}>
        {children}
      </div>
    </div>
  );
}

export function Card({
  title,
  actions,
  footer,
  children,
  className,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cx(
        "rounded-lg border border-stone-200 bg-white p-4 shadow-sm dark:border-stone-800 dark:bg-stone-900",
        className,
      )}
    >
      {(title || actions) && (
        <header className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="text-base font-semibold">{title}</h2>}
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      {children}
      {footer && <footer className="mt-3 text-xs text-muted">{footer}</footer>}
    </section>
  );
}

type Tone = "neutral" | "teal" | "amber" | "red" | "blue" | "violet";

const TONES: Record<Tone, string> = {
  neutral: "bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300",
  teal: "bg-teal-100 text-teal-800 dark:bg-teal-900/50 dark:text-teal-200",
  amber: "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-200",
  red: "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-200",
  blue: "bg-sky-100 text-sky-800 dark:bg-sky-900/50 dark:text-sky-200",
  violet: "bg-violet-100 text-violet-800 dark:bg-violet-900/50 dark:text-violet-200",
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={cx("inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium", TONES[tone])}>
      {children}
    </span>
  );
}

/**
 * A 24 × 24 px tap area around a small checkbox (WCAG 2.5.8): a click anywhere in it toggles
 * the box. The checkbox keeps its own aria-label (the wrapping label has no text).
 */
export function CheckboxTarget({ children }: { children: ReactNode }) {
  return (
    <label className="flex size-6 shrink-0 cursor-pointer items-center justify-center has-disabled:cursor-not-allowed">
      {children}
    </label>
  );
}

export function Alert({
  tone = "info",
  children,
}: {
  tone?: "info" | "error" | "warning" | "success";
  children: ReactNode;
}) {
  const styles = {
    info: "border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-100",
    error: "border-red-200 bg-red-50 text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100",
    warning:
      "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100",
    success:
      "border-teal-200 bg-teal-50 text-teal-900 dark:border-teal-900 dark:bg-teal-950 dark:text-teal-100",
  }[tone];
  return (
    <div role={tone === "error" ? "alert" : "status"} className={cx("rounded-md border px-3 py-2 text-sm", styles)}>
      {children}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-4 text-center text-sm text-muted">{children}</p>;
}

export function Spinner({ label = "Chargement…" }: { label?: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-sm text-muted">
      <span
        className="size-4 animate-spin rounded-full border-2 border-stone-300 border-t-teal-700 motion-reduce:animate-none"
        aria-hidden
      />
      {label}
    </span>
  );
}

/** The document title of a page: « Tâches · LifeOS » (read by the route announcer). */
export function documentTitle(page: string): string {
  return `${page} · LifeOS`;
}

/**
 * The page's h1. It also sets the document title (React hoists <title> into <head>), so each
 * page has its own title (WCAG 2.4.2) and the Next.js route announcer reads it on navigation.
 * `documentTitle` overrides it when the heading carries volatile details (a date…).
 */
export function PageHeader({
  title,
  documentTitle: page,
  children,
}: {
  title: string;
  documentTitle?: string;
  children?: ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <title>{documentTitle(page ?? title)}</title>
      <h1 className="text-xl font-semibold">{title}</h1>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

export { cx };
