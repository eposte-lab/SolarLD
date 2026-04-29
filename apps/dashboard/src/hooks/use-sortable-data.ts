"use client";

import { useCallback, useMemo, useState } from "react";

export type SortDir = "asc" | "desc";

/**
 * Generic sortable-table hook.
 *
 * Pass a list of rows and a `getValue` extractor that, given a row + a sort
 * key (the column id), returns either a `number`, a `string`, a `Date`, or
 * `null`/`undefined` (sorted to the end). Click handler `requestSort(key)`
 * toggles direction when re-clicking the active key.
 *
 * Strings are compared with Italian locale collation; numbers/Dates compared
 * numerically. Stable for ties via index fallback.
 *
 * Reference: extracted from
 * `components/dashboard/lead-temperature-board.tsx` so we do not have to
 * re-implement the same boilerplate in every page.
 */
export function useSortableData<T, K extends string>(
  items: readonly T[],
  getValue: (row: T, key: K) => number | string | Date | null | undefined,
  options: {
    initialKey?: K | null;
    initialDir?: SortDir;
  } = {},
): {
  sorted: T[];
  sortKey: K | null;
  sortDir: SortDir;
  requestSort: (key: K) => void;
} {
  const { initialKey = null, initialDir = "desc" } = options;
  const [sortKey, setSortKey] = useState<K | null>(initialKey);
  const [sortDir, setSortDir] = useState<SortDir>(initialDir);

  const requestSort = useCallback(
    (key: K) => {
      setSortKey((prev) => {
        if (prev === key) {
          // toggle direction
          setSortDir((d) => (d === "asc" ? "desc" : "asc"));
          return prev;
        }
        // new column → default to desc (most recent / highest first)
        setSortDir("desc");
        return key;
      });
    },
    [],
  );

  const sorted = useMemo(() => {
    if (!sortKey) return [...items];
    const arr = items.map((row, idx) => ({ row, idx }));
    arr.sort((a, b) => {
      const aRaw = getValue(a.row, sortKey);
      const bRaw = getValue(b.row, sortKey);

      // null/undefined → always sort to the end regardless of dir
      const aNull = aRaw === null || aRaw === undefined || aRaw === "";
      const bNull = bRaw === null || bRaw === undefined || bRaw === "";
      if (aNull && bNull) return a.idx - b.idx;
      if (aNull) return 1;
      if (bNull) return -1;

      let cmp: number;
      if (aRaw instanceof Date && bRaw instanceof Date) {
        cmp = aRaw.getTime() - bRaw.getTime();
      } else if (typeof aRaw === "number" && typeof bRaw === "number") {
        cmp = aRaw - bRaw;
      } else {
        cmp = String(aRaw).localeCompare(String(bRaw), "it", {
          sensitivity: "base",
          numeric: true,
        });
      }

      if (cmp === 0) return a.idx - b.idx; // stable tie-break
      return sortDir === "asc" ? cmp : -cmp;
    });
    return arr.map((x) => x.row);
  }, [items, sortKey, sortDir, getValue]);

  return { sorted, sortKey, sortDir, requestSort };
}
