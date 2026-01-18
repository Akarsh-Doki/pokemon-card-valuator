import { useMemo, useRef, useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  ResponsiveContainer,
  Legend,
  Brush,
  Tooltip,
} from "recharts";
import { motion, AnimatePresence } from "framer-motion";

type HistoryPoint = { date: string; price: number };
type HistorySeries = { name: string; points: HistoryPoint[] };

type ParsedHistory = {
  arr: Array<Record<string, string | number>>;
  names: string[];
};

function parseSeries(history: any): ParsedHistory {
  const series: HistorySeries[] = Array.isArray(history?.series) ? history.series : [];

  const allDates = new Set<string>();
  const map: Record<string, Record<string, string | number>> = {};

  for (const s of series) {
    const name = String(s?.name || "").trim();
    if (!name || !Array.isArray(s.points)) continue;

    for (const p of s.points) {
      if (!p?.date) continue;
      allDates.add(p.date);

      map[p.date] = map[p.date] || { date: p.date };
      map[p.date][name] = Number(p.price);
    }
  }

  const arr = Array.from(allDates)
    .sort()
    .map((d) => map[d]);

  const names = series
    .map((s) => String(s?.name || "").trim())
    .filter(Boolean);

  return { arr, names };
}

function filterRange(data: Array<Record<string, string | number>>, range: string) {
  if (!data.length) return data;
  if (range === "ALL") return data;

  const days = range === "7D" ? 7 : range === "30D" ? 30 : 365;

  const last = data[data.length - 1]?.date;
  if (typeof last !== "string") return data;

  const cutoff = new Date(last).getTime() - days * 86400000;

  return data.filter((d) => {
    const dt = d.date;
    if (typeof dt !== "string") return false;
    return new Date(dt).getTime() >= cutoff;
  });
}

function fmtUSD(n: any) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  return `$${Number(n).toFixed(2)}`;
}

function CrosshairCursor(props: any) {
  const { points, viewBox } = props;
  if (!points?.length || !viewBox) return null;

  const x = points[0]?.x;
  const y = points[0]?.y;

  if (typeof x !== "number" || typeof y !== "number") return null;

  const left = viewBox.x;
  const right = viewBox.x + viewBox.width;
  const top = viewBox.y;
  const bottom = viewBox.y + viewBox.height;

  return (
    <g>
      <line
        x1={x}
        x2={x}
        y1={top}
        y2={bottom}
        stroke="rgba(255,255,255,0.25)"
        strokeDasharray="4 4"
        strokeWidth={1}
      />
      <line
        x1={left}
        x2={right}
        y1={y}
        y2={y}
        stroke="rgba(255,255,255,0.18)"
        strokeDasharray="4 4"
        strokeWidth={1}
      />
    </g>
  );
}

type HoverState = { date: string; values: Record<string, number> } | null;

export default function PriceHistoryChart({ history }: { history: any }) {
  const [range, setRange] = useState<"7D" | "30D" | "1Y" | "ALL">("ALL");

  const [seriesPick, setSeriesPick] = useState<string>("ALL");

  const [hover, setHover] = useState<HoverState>(null);
  const [fullscreen, setFullscreen] = useState(false);

  const { arr, names } = useMemo(() => parseSeries(history), [history]);
  const data = useMemo(() => filterRange(arr, range), [arr, range]);

  const hasAnyData = data.length > 0 && names.length > 0;

  const visibleNames = useMemo(() => {
    if (seriesPick === "ALL") return names;
    if (!names.includes(seriesPick)) return names;
    return [seriesPick];
  }, [names, seriesPick]);

  const rafRef = useRef<number | null>(null);
  const lastRef = useRef<string>("");

  const scheduleHoverUpdate = (next: HoverState) => {
    const key = next ? `${next.date}|${JSON.stringify(next.values)}` : "null";
    if (key === lastRef.current) return;
    lastRef.current = key;

    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(() => {
      setHover(next);
    });
  };

  function TooltipBridge(props: any) {
    const { active, label, payload } = props;

    if (active && label && Array.isArray(payload)) {
      const values: Record<string, number> = {};

      for (const p of payload) {
        const key = String(p?.dataKey || "");
        const val = Number(p?.value);
        if (key && !Number.isNaN(val)) values[key] = val;
      }

      scheduleHoverUpdate({ date: String(label), values });
    }

    return null; // invisible tooltip box
  }

  const ChartCore = ({ height }: { height: number }) => (
    <div style={{ height }} className="w-full">
      {!hasAnyData ? (
        <div className="h-full flex items-center justify-center text-sm text-white/55">
          No price history available for this variant.
        </div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={data}
            onMouseLeave={() => scheduleHoverUpdate(null)}
          >
            <XAxis
              dataKey="date"
              tick={{ fill: "rgba(255,255,255,0.55)", fontSize: 11 }}
            />
            <YAxis tick={{ fill: "rgba(255,255,255,0.55)", fontSize: 11 }} />

            <Tooltip
              content={<TooltipBridge />}
              cursor={<CrosshairCursor />}
              wrapperStyle={{ display: "none" }} // ✅ prevents any tooltip blackout box
            />

            <Legend />

            {visibleNames.map((n) => (
              <Line
                key={n}
                type="monotone"
                dataKey={n}
                dot={false}
                activeDot={{ r: 5 }} 
                strokeWidth={2.4}
                isAnimationActive={false} 
              />
            ))}

            <Brush dataKey="date" height={22} />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );

  return (
    <>
      <div className="rounded-2xl border border-white/10 bg-black/25 p-4 relative overflow-hidden">
        <div className="flex items-center justify-between mb-3">
          <div className="text-xs text-white/60">Range</div>

          <div className="flex items-center gap-2">
            {(["7D", "30D", "1Y", "ALL"] as const).map((r) => (
              <button
                key={r}
                onClick={() => setRange(r)}
                className={`px-3 py-1 rounded-lg text-xs border transition
                  ${
                    range === r
                      ? "bg-white/15 border-fuchsia-300/30"
                      : "bg-white/5 border-white/10 hover:bg-white/10"
                  }
                `}
              >
                {r}
              </button>
            ))}

            <button
              onClick={() => setFullscreen(true)}
              className="ml-2 px-3 py-1 rounded-lg text-xs border bg-white/5 border-white/10 hover:bg-white/10 transition"
            >
              Fullscreen
            </button>
          </div>
        </div>

        <div className="mb-4">
          <div className="text-[11px] text-white/55 mb-2">Series</div>

          <div className="flex gap-2 overflow-x-auto pb-2">
            <button
              onClick={() => setSeriesPick("ALL")}
              className={`shrink-0 px-3 py-1 rounded-lg text-xs border transition
                ${
                  seriesPick === "ALL"
                    ? "bg-white/15 border-fuchsia-300/30"
                    : "bg-white/5 border-white/10 hover:bg-white/10"
                }
              `}
            >
              ALL
            </button>

            {names.map((n) => (
              <button
                key={n}
                onClick={() => setSeriesPick(n)}
                className={`shrink-0 px-3 py-1 rounded-lg text-xs border transition
                  ${
                    seriesPick === n
                      ? "bg-white/15 border-fuchsia-300/30"
                      : "bg-white/5 border-white/10 hover:bg-white/10"
                  }
                `}
              >
                {n}
              </button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-[1fr_240px] gap-4">
          {/* Chart */}
          <div className="h-[280px] w-full">
            <ChartCore height={280} />
          </div>

          {/* Side Inspector */}
          <div className="rounded-2xl border border-white/10 bg-black/25 p-3 h-[280px] overflow-hidden">
            <div className="text-xs text-white/60">Hover Inspector</div>

            {!hover ? (
              <div className="mt-2 text-sm text-white/50">
                Hover on the graph to view prices.
              </div>
            ) : (
              <div className="mt-2">
                <div className="text-sm font-semibold text-white/90">{hover.date}</div>

                <div className="mt-3 space-y-2 text-sm">
                  {visibleNames.map((n) => (
                    <div key={n} className="flex items-center justify-between gap-3">
                      <div className="text-white/60 truncate">{n}</div>
                      <div className="font-semibold">{fmtUSD(hover.values[n])}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="mt-2 text-[11px] text-white/50">
          Hover to inspect prices • Crosshair tracks cursor • Brush to zoom • “ALL” shows full history
        </div>
      </div>

      {/* ---------- Fullscreen Modal ---------- */}
      <AnimatePresence>
        {fullscreen && (
          <motion.div
            className="fixed inset-0 z-[9999] bg-black/70 flex items-center justify-center p-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <motion.div
              className="w-full max-w-[96vw] rounded-3xl border border-white/15 bg-[#090014]/90 backdrop-blur-xl p-6 shadow-2xl"
              initial={{ scale: 0.96, y: 18 }}
              animate={{ scale: 1, y: 0 }}
              exit={{ scale: 0.96, y: 18 }}
              transition={{ type: "spring", stiffness: 180, damping: 18 }}
            >
              <div className="flex items-center justify-between mb-4">
                <div>
                  <div className="text-sm text-white/60">Price History</div>
                  <div className="text-xl font-semibold">Fullscreen View</div>
                </div>

                <button
                  onClick={() => setFullscreen(false)}
                  className="px-4 py-2 rounded-xl bg-white/10 border border-white/15 hover:bg-white/15 transition text-sm"
                >
                  Close
                </button>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-4">
                <div className="h-[760px] w-full rounded-2xl border border-white/10 bg-black/25 p-2">
                  <ChartCore height={760} />
                </div>

                <div className="rounded-2xl border border-white/10 bg-black/25 p-4 h-[760px] overflow-hidden">
                  <div className="text-xs text-white/60">Hover Inspector</div>

                  {!hover ? (
                    <div className="mt-2 text-sm text-white/50">
                      Hover on the chart to see prices.
                    </div>
                  ) : (
                    <div className="mt-2">
                      <div className="text-sm font-semibold text-white/90">{hover.date}</div>

                      <div className="mt-3 space-y-2 text-sm">
                        {visibleNames.map((n) => (
                          <div key={n} className="flex items-center justify-between gap-3">
                            <div className="text-white/60 truncate">{n}</div>
                            <div className="font-semibold">{fmtUSD(hover.values[n])}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  <div className="mt-4 text-[12px] text-white/45">
                    Tip: Use ALL for full history. Brush to zoom.
                  </div>
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
