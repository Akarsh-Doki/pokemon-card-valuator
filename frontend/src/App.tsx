import { Routes, Route } from "react-router-dom";
import HomePage from "./pages/HomePage";
import ResultPage from "./pages/ResultPage";

export default function App() {
  return (
    <div className="min-h-screen aurora-bg text-white">
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/result/:jobId" element={<ResultPage />} />
      </Routes>
    </div>
  );
}
