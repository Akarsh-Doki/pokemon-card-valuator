import { useState } from "react";
import { motion } from "framer-motion";
import { Camera, UploadCloud } from "lucide-react";

export default function DropZone({ onFile }: { onFile: (file: File) => void }) {
  const [drag, setDrag] = useState(false);

  return (
    <motion.div
      whileHover={{ scale: 1.01 }}
      className={`relative rounded-2xl border transition-all p-10 flex flex-col items-center justify-center text-center cursor-pointer
        ${drag ? "border-fuchsia-400/60 bg-white/5" : "border-white/15 bg-white/0"}
      `}
      onDragOver={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        const f = e.dataTransfer.files?.[0];
        if (f) onFile(f);
      }}
      onClick={() => document.getElementById("filepicker")?.click()}
    >
      <motion.div
        animate={{ opacity: drag ? 1 : 0.9 }}
        className="flex items-center gap-2 text-white/85"
      >
        <UploadCloud className="w-5 h-5" />
        <span className="font-medium">Drag & drop a card image</span>
      </motion.div>

      <div className="text-white/55 text-sm mt-2">
        or click to upload from camera roll
      </div>

      <div className="mt-5 flex gap-3">
        <motion.button
          whileTap={{ scale: 0.98 }}
          className="px-4 py-2 rounded-xl bg-white/10 border border-white/15 hover:bg-white/15 transition flex items-center gap-2"
          onClick={(e) => {
            e.stopPropagation();
            document.getElementById("filepicker")?.click();
          }}
        >
          <Camera className="w-4 h-4" />
          <span>Upload</span>
        </motion.button>
      </div>

      <input
        id="filepicker"
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
        }}
      />
    </motion.div>
  );
}
