import { cx } from "@/components/ui";

/**
 * On/off switch (role="switch"). Give it an accessible name with `label` or
 * `labelledBy`; `describedBy` points to the explanation of what it changes.
 */
export function UsageSwitch({
  checked,
  onChange,
  disabled,
  label,
  labelledBy,
  describedBy,
  tone = "teal",
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label?: string;
  labelledBy?: string;
  describedBy?: string;
  /** Colour when on: amber for an emergency stop. */
  tone?: "teal" | "amber";
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx(
        "relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 disabled:cursor-wait disabled:opacity-60",
        checked ? (tone === "amber" ? "bg-amber-600" : "bg-teal-700") : "bg-stone-300 dark:bg-stone-700",
      )}
    >
      <span
        aria-hidden
        className={cx(
          "inline-block size-6 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-5" : "translate-x-0.5",
        )}
      />
    </button>
  );
}
