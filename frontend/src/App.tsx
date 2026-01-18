import { BrowserRouter, Routes, Route } from "react-router-dom";
import HomePage from "./pages/HomePage.tsx";
import ResultPage from "./pages/ResultPage.tsx";
import PriceHistoryChart from "./components/PriceHistoryChart";


export default function App() {
  return (
    <div className="min-h-screen aurora-bg text-white">
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/result/:jobId" element={<ResultPage />} />
        </Routes>
      </BrowserRouter>
    </div>
  );
}

function pickPsaLadder(prices: any) {
  if (!prices) return null;

  const num = (x: any) => {
    const n = typeof x === "string" ? parseFloat(x) : x;
    return Number.isFinite(n) ? n : null;
  };

  return {
    ungraded: num(prices.ungraded ?? prices.raw ?? prices.normal ?? prices.marketPrice),
    psa7: num(prices.psa7 ?? prices.grade7 ?? prices.psa_7),
    psa8: num(prices.psa8 ?? prices.grade8 ?? prices.psa_8),
    psa9: num(prices.psa9 ?? prices.grade9 ?? prices.psa_9),
    psa95: num(prices.psa95 ?? prices.grade95 ?? prices.psa_95),
    psa10: num(prices.psa10 ?? prices.grade10 ?? prices.psa_10),
  };
}

