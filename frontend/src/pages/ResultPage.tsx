import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { fetchHistory, sendFeedback, sseUrl } from "../api";
import LoadingOverlay from "../components/LoadingOverlay";
import PriceHistoryChart from "../components/PriceHistoryChart";
import VariantPicker from "../components/VariantPicker";

type LadderKey = "ungraded" | "psa7" | "psa8" | "psa9" | "psa95" | "psa10";
type PsaLadder = Partial<Record<LadderKey, number | null>>;

export default function ResultPage() {
  const { jobId } = useParams();

  const [stage, setStage] = useState("Starting");
  const [detail, setDetail] = useState("Preparing scan…");
  const [loading, setLoading] = useState(true);

  const [result, setResult] = useState<any>(null);
  const [selectedVariant, setSelectedVariant] = useState<any>(null);

  const [history, setHistory] = useState<any>(null);
  const [historyError, setHistoryError] = useState<string>("");

  const preview = useMemo(() => {
    if (!jobId) return "";
    return sessionStorage.getItem(`preview:${jobId}`) || "";
  }, [jobId]);

  useEffect(() => {
    if (!jobId) return;

    const es = new EventSource(sseUrl(jobId));

    es.addEventListener("progress", (ev) => {
      const msg = JSON.parse((ev as MessageEvent).data);
      setStage(msg.stage || "Working…");
      setDetail(msg.detail || "");
      setLoading(true);
    });

    es.addEventListener("result", (ev) => {
      const payload = JSON.parse((ev as MessageEvent).data);
      setResult(payload);
      setLoading(false);

      const pricing = payload?.debug?.pricing ?? {};
      const pc = payload?.debug?.pricecharting ?? pricing?.pricecharting ?? {};

      const v = Array.isArray(pc?.variants) ? pc.variants : [];
      if (v.length > 0) setSelectedVariant(v[0]);

      es.close();
    });

    es.addEventListener("error", () => {
      setLoading(false);
      setStage("Scan failed");
      setDetail("Please try a clearer photo or different lighting.");
      es.close();
    });

    return () => es.close();
  }, [jobId]);

  useEffect(() => {
    const loadHistory = async () => {
      setHistory(null);
      setHistoryError("");

      if (!selectedVariant?.url) return;

      try {
        const data = await fetchHistory(selectedVariant.url);

        if (!data?.series || !Array.isArray(data.series) || data.series.length === 0) {
          setHistory(null);
          setHistoryError("No price history available for this variant.");
          return;
        }

        setHistory(data);
      } catch (e) {
        setHistory(null);
        setHistoryError("History unavailable for this variant.");
      }
    };

    loadHistory();
  }, [selectedVariant]);

  const pricing = result?.debug?.pricing ?? {};
  const pc = result?.debug?.pricecharting ?? pricing?.pricecharting ?? {};

  const variants = Array.isArray(pc?.variants) ? pc.variants : [];

  function pickPsaLadder(prices: any): PsaLadder {
    if (!prices) return {};
    return {
      ungraded: prices.ungraded ?? null,
      psa7: prices.psa7 ?? prices.grade7 ?? null,
      psa8: prices.psa8 ?? null,
      psa9: prices.psa9 ?? null,
      psa95: prices.psa95 ?? prices.grade95 ?? null,
      psa10: prices.psa10 ?? null,
    };
  }

  const selectedPrices = selectedVariant?.prices ?? variants?.[0]?.prices ?? null;
  const ladder = pickPsaLadder(selectedPrices);

  const gradeKeys: LadderKey[] = ["ungraded", "psa7", "psa8", "psa9", "psa95", "psa10"];

  return (
    <div className="min-h-screen px-6 py-10">
      <div className="max-w-6xl mx-auto glass soft-shadow rounded-3xl p-6 md:p-8 relative overflow-hidden">
        {loading && <LoadingOverlay stage={stage} detail={detail} />}

        {/* Top header */}
        <div className="flex items-start justify-between gap-6">
          <div>
            <div className="text-sm text-white/60">Pokemon Valuator</div>
            <div className="text-2xl font-semibold mt-1">
              {result?.card_name || "Scanning…"}
            </div>
            <div className="text-white/60 mt-1 text-sm">
              {result?.set_name || ""}{" "}
              {result?.card_number ? `• ${result.card_number}` : ""}
            </div>
            <div className="text-xs text-white/45 mt-2">
              Status: {result?.status || "running"} • Confidence:{" "}
              {Math.round((result?.confidence || 0) * 100)}%
            </div>
          </div>

          <button
            className="px-4 py-2 rounded-xl bg-white/10 border border-white/15 hover:bg-white/15 transition text-sm"
            onClick={() => (window.location.href = "/")}
          >
            Close
          </button>
        </div>

        <div className="mt-8 grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Left */}
          <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
            <div className="text-sm text-white/70 mb-3">Captured Image</div>

            {preview ? (
              <img
                src={preview}
                className="rounded-xl w-full object-contain max-h-[520px]"
                alt="Captured card"
              />
            ) : (
              <div className="text-white/50 text-sm">No preview available.</div>
            )}

            <button
              className="mt-4 w-full px-4 py-2 rounded-xl bg-white/10 border border-white/15 hover:bg-white/15 transition"
              onClick={() => (window.location.href = "/")}
            >
              Scan Again
            </button>
          </div>

          <div className="rounded-2xl border border-white/10 bg-white/5 p-5">
            <div className="text-sm text-white/70 mb-3">Variants</div>

            <VariantPicker
              variants={variants}
              selected={selectedVariant}
              onSelect={setSelectedVariant}
            />

            <div className="mt-6">
              <div className="text-sm text-white/70 mb-3">PSA Ladder</div>

              <div className="grid grid-cols-2 gap-3 text-sm">
                {gradeKeys.map((k) => (
                  <div
                    key={k}
                    className="rounded-xl border border-white/10 bg-black/20 px-4 py-3 flex justify-between"
                  >
                    <span className="uppercase text-white/55">{k}</span>
                    <span className="font-semibold">
                      {ladder[k] != null ? `$${ladder[k]}` : "—"}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <div className="mt-7">
              <div className="text-sm text-white/70 mb-2">
                Price History (Interactive)
              </div>

              {historyError ? (
                <div className="rounded-xl border border-white/10 bg-black/20 p-4 text-sm text-white/60">
                  {historyError}
                </div>
              ) : history ? (
                <PriceHistoryChart history={history} />
              ) : (
                <div className="rounded-xl border border-white/10 bg-black/20 p-4 text-sm text-white/60">
                  Loading history…
                </div>
              )}
            </div>

            <div className="mt-8 border-t border-white/10 pt-5">
              <div className="text-sm font-medium">Was this the right card?</div>
              <div className="text-white/60 text-sm mt-1">
                Confirming improves caching + future scans.
              </div>

              <div className="flex gap-3 mt-4">
                <button
                  className="px-4 py-2 rounded-xl bg-emerald-500/90 hover:bg-emerald-500 transition text-sm font-medium"
                  onClick={async () => {
                    await sendFeedback({
                      image_hash: result?.image_hash,
                      correct: true,
                      chosen_variant_url: selectedVariant?.url,
                    });
                    alert("Saved ✅");
                  }}
                >
                  Yes, correct
                </button>

                <button
                  className="px-4 py-2 rounded-xl bg-white/10 border border-white/15 hover:bg-white/15 transition text-sm"
                  onClick={async () => {
                    await sendFeedback({
                      image_hash: result?.image_hash,
                      correct: false,
                    });
                    alert("Not saved ❌");
                  }}
                >
                  No, wrong
                </button>
              </div>
            </div>
          </div>
        </div>

        <div className="mt-6 text-xs text-white/40">
          Note: Price history availability depends on PriceCharting pages for the selected variant.
        </div>
      </div>
    </div>
  );
}
