import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
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
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" }) {
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

const FIELD =
  "w-full rounded-md border border-stone-300 bg-white px-3 py-2 text-sm text-stone-900 placeholder:text-stone-400 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-100";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx(FIELD, className)} {...props} />;
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx(FIELD, "min-h-24", className)} {...props} />;
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
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
  return (
    <label className={cx("flex flex-col gap-1 text-sm", className)}>
      <span className="font-medium text-stone-700 dark:text-stone-300">{label}</span>
      {children}
      {hint && <span className="text-xs text-stone-500">{hint}</span>}
    </label>
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
      {footer && <footer className="mt-3 text-xs text-stone-500">{footer}</footer>}
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
  return <p className="py-4 text-center text-sm text-stone-500">{children}</p>;
}

export function Spinner({ label = "Chargement…" }: { label?: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-sm text-stone-500">
      <span className="size-4 animate-spin rounded-full border-2 border-stone-300 border-t-teal-700" aria-hidden />
      {label}
    </span>
  );
}

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <h1 className="text-xl font-semibold">{title}</h1>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

export { cx };
