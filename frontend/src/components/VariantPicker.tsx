import { Sparkles, Repeat, Tag } from "lucide-react";
import { motion } from "framer-motion";

function iconForVariant(label: string) {
  const t = (label || "").toLowerCase();
  if (t.includes("reverse")) return <Repeat className="w-4 h-4" />;
  if (t.includes("holo")) return <Sparkles className="w-4 h-4" />;
  return <Tag className="w-4 h-4" />;
}

export default function VariantPicker({
  variants,
  selected,
  onSelect,
}: {
  variants: any[];
  selected: any;
  onSelect: (v: any) => void;
}) {
  return (
    <div className="space-y-2 max-h-[260px] overflow-auto pr-1">
      {variants.map((v) => {
        const active = selected?.url === v.url;
        return (
          <motion.button
            key={v.url}
            whileTap={{ scale: 0.99 }}
            onClick={() => onSelect(v)}
            className={`w-full text-left rounded-xl px-4 py-3 border transition flex items-center justify-between gap-3
              ${active ? "bg-white/15 border-fuchsia-300/30" : "bg-black/20 border-white/10 hover:bg-white/10"}
            `}
          >
            <div className="flex items-center gap-3">
              <div className="text-white/85">{iconForVariant(v.label || v.title || "")}</div>
              <div>
                <div className="text-sm font-medium text-white/90 line-clamp-1">
                  {v.title || v.label || "Variant"}
                </div>
                <div className="text-xs text-white/50 line-clamp-1">
                  {v.url}
                </div>
              </div>
            </div>

            <div className="text-xs text-white/60">
              {active ? "Selected" : ""}
            </div>
          </motion.button>
        );
      })}
    </div>
  );
}
