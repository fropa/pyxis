import clsx from "clsx";

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={clsx(
        "rounded-md bg-gradient-to-r from-slate-100 via-slate-200 to-slate-100 bg-[length:400%_100%] animate-pulse",
        className
      )}
    />
  );
}

export function SkeletonText({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          className={clsx("h-3", i === lines - 1 ? "w-3/5" : "w-full")}
        />
      ))}
    </div>
  );
}

export function SkeletonCard() {
  return (
    <div className="bg-surface border border-border rounded-xl p-5 space-y-3 shadow-card">
      <div className="flex items-center justify-between">
        <Skeleton className="h-3.5 w-24" />
        <Skeleton className="h-9 w-9 rounded-xl" />
      </div>
      <Skeleton className="h-8 w-16" />
      <Skeleton className="h-3 w-32" />
    </div>
  );
}
