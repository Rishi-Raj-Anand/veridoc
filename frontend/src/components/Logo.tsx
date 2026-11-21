/** The VeriDoc mark: a shield with a check, inline SVG so nothing loads from outside. */

export function LogoMark({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <path
        d="M16 2.5 5 6.6v8.2c0 6.7 4.5 12.2 11 14.7 6.5-2.5 11-8 11-14.7V6.6L16 2.5Z"
        className="fill-teal-600"
      />
      <path
        d="m10.5 16.2 4 4 7.2-8.2"
        fill="none"
        stroke="white"
        strokeWidth="2.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Logo({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 font-bold tracking-tight ${className}`}>
      <LogoMark />
      <span>
        VeriDoc <span className="text-teal-600">AI</span>
      </span>
    </span>
  );
}
