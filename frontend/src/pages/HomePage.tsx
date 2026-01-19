import { motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { pingApi, startIdentify } from "../api";
import DropZone from "../components/DropZone";
import PokeballIcon from "../components/PokeballIcon";

function readFileAsDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result || ""));
    r.onerror = () => reject(new Error("Failed to read file"));
    r.readAsDataURL(file);
  });
}

export default function HomePage() {
  const navigate = useNavigate();

  const handleFile = async (file: File) => {
    try {
      const preview = await readFileAsDataURL(file);

      const ok = await pingApi();

      if (!ok) {
        sessionStorage.setItem(`preview:demo`, preview);
        navigate(`/result/demo`);
        return;
      }

      const { job_id } = await startIdentify(file);

      sessionStorage.setItem(`preview:${job_id}`, preview);

      navigate(`/result/${job_id}`);
    } catch (e) {
      console.error(e);
      alert("Upload failed. Make sure the API is running, then try again.");
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center px-6 py-12">
      <div className="w-full max-w-5xl glass soft-shadow rounded-3xl p-8 md:p-10">
        <div className="flex items-start justify-between gap-6">
          <div>
            <motion.h1
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className="text-3xl md:text-4xl font-semibold tracking-tight flex items-center gap-2"
            >
              <PokeballIcon size={27} />
              Pokemon Valuator
            </motion.h1>

            <motion.p
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="text-white/70 mt-2"
            >
              Scan a card → detect metadata → pick the exact variant → see PSA
              pricing + history.
            </motion.p>
          </div>

          <div className="hidden md:block text-white/60 text-sm">
            Built for fast high-accuracy price discovery.
          </div>
        </div>

        <div className="mt-8">
          <DropZone onFile={handleFile} />
        </div>

        <div className="mt-6 text-xs text-white/50">
          Tip: Use good lighting + hold the card flat. Trainer cards may be less
          accurate (in progress).
        </div>
      </div>
    </div>
  );
}
