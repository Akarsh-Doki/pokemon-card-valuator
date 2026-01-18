import { motion } from "framer-motion";

export default function LoadingOverlay({ stage, detail }: { stage: string; detail: string }) {
  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/55 backdrop-blur-md" />

      <motion.div
        initial={{ opacity: 0, y: 10, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        className="relative z-10 glass rounded-2xl px-6 py-5 border border-white/10 w-[340px] text-center"
      >
        <motion.div
          animate={{ rotate: 360 }}
          transition={{ repeat: Infinity, duration: 1.1, ease: "linear" }}
          className="w-10 h-10 rounded-full border-2 border-white/20 border-t-white mx-auto"
        />

        <div className="mt-4 text-sm font-semibold">{stage}</div>
        <div className="mt-1 text-xs text-white/60">{detail}</div>

        <div className="mt-4 text-[11px] text-white/40">
          Keep the tab open — progress updates live.
        </div>
      </motion.div>
    </div>
  );
}
