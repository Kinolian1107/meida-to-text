import { Link, Navigate, Route, Routes } from "react-router-dom";
import UploadPage from "./pages/UploadPage";
import LibraryPage from "./pages/LibraryPage";
import ProgressPage from "./pages/ProgressPage";
import TimelinePage from "./pages/TimelinePage";
import SummaryPage from "./pages/SummaryPage";
import AccountsPage from "./pages/AccountsPage";
import CrossPage from "./pages/CrossPage";
import PromptsPage from "./pages/PromptsPage";

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          media2text
        </Link>
        <nav>
          <Link to="/">上傳</Link>
          <Link to="/library">項目庫</Link>
          <Link to="/cross">跨檔彙整</Link>
          <Link to="/prompts">Prompt</Link>
          <Link to="/accounts">帳號</Link>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<UploadPage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/cross" element={<CrossPage />} />
          <Route path="/prompts" element={<PromptsPage />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/progress/:id" element={<ProgressPage />} />
          <Route path="/timeline/:id" element={<TimelinePage />} />
          <Route path="/summary/:id" element={<SummaryPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
