"use client";

import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";
import type { SortDir } from "@/hooks/use-sortable-data";

interface SortableThProps<K extends string> {
  /** The column key as recognized by `useSortableData`'s `getValue`. */
  sortKey: K;
  /** Currently active sort key (or null). */
  active: K | null;
  /** Currently active direction. */
  dir: SortDir;
  /** Click handler — typically `requestSort` from the hook. */
  onSort: (key: K) => void;
  className?: string;
  /** Right-align content (numeric columns). */
  align?: "left" | "right" | "center";
  children: ReactNode;
}

/**
 * Clickable `<th>` cell that toggles a column's sort. Pairs with
 * `useSortableData`. Renders a Lucide arrow indicator that reflects the
 * current `(active, dir)` state.
 */
export function SortableTh<K extends string>({
  sortKey,
  active,
  dir,
  onSort,
  className,
  align = "left",
  children,
}: SortableThProps<K>) {
  const isActive = active === sortKey;
  const Icon = !isActive ? ArrowUpDown : dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <th
      scope="col"
      onClick={() => onSort(sortKey)}
      className={cn(
        "cursor-pointer select-none whitespace-nowrap px-4 py-3 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant transition-colors hover:text-on-surface",
        align === "right" && "text-right",
        align === "center" && "text-center",
        align === "left" && "text-left",
        isActive && "text-on-surface",
        className,
      )}
      aria-sort={
        isActive ? (dir === "asc" ? "ascending" : "descending") : "none"
      }
    >
      <span className="inline-flex items-center gap-1">
        {children}
        <Icon
          size={11}
          strokeWidth={isActive ? 2.5 : 2}
          className={cn(
            "shrink-0",
            isActive ? "text-primary" : "text-on-surface-variant/40",
          )}
          aria-hidden
        />
      </span>
    </th>
  );
}
