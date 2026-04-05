interface QueryErrorStateProps {
  title?: string;
  message: string;
  className?: string;
}

export function QueryErrorState({
  title = "Unable to load data",
  message,
  className = "",
}: QueryErrorStateProps) {
  return (
    <div
      className={`rounded-xl border border-danger-border bg-danger-bg px-4 py-3 ${className}`.trim()}
    >
      <p className="text-[13px] font-semibold text-danger-text">{title}</p>
      <p className="mt-1 text-[12px] leading-relaxed text-danger-text/90">{message}</p>
    </div>
  );
}
