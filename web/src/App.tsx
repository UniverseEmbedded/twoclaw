import { Routes, Route, Navigate } from 'react-router-dom';
import { useState, useEffect, createContext, useContext } from 'react';
import Layout from './components/layout/Layout';
import Dashboard from './pages/Dashboard';
import AgentChat from './pages/AgentChat';
import Tools from './pages/Tools';
import Cron from './pages/Cron';
import Integrations from './pages/Integrations';
import Memory from './pages/Memory';
import Config from './pages/Config';
import Cost from './pages/Cost';
import Logs from './pages/Logs';
import Doctor from './pages/Doctor';
import { AuthProvider, useAuth } from './hooks/useAuth';
import { setLocale, tLocale, type Locale } from './lib/i18n';
import { getStatus } from './lib/api';

// Locale context
interface LocaleContextType {
  locale: Locale;
  setAppLocale: (locale: Locale) => void;
}

export const LocaleContext = createContext<LocaleContextType>({
  locale: 'en',
  setAppLocale: () => {},
});

export const useLocaleContext = () => useContext(LocaleContext);

const LOCALE_KEY = 'zeroclaw_locale';

function normalizeLocale(value: string | null | undefined): Locale | null {
  const v = (value ?? '').toLowerCase();
  if (v.startsWith('zh')) return 'zh';
  if (v.startsWith('tr')) return 'tr';
  if (v.startsWith('en')) return 'en';
  return null;
}

function loadStoredLocale(): Locale | null {
  try {
    return normalizeLocale(localStorage.getItem(LOCALE_KEY));
  } catch {
    return null;
  }
}

function saveStoredLocale(locale: Locale): void {
  try {
    localStorage.setItem(LOCALE_KEY, locale);
  } catch {
    // Ignore
  }
}

// Pairing dialog component
function PairingDialog({ onPair }: { onPair: (code: string) => Promise<void> }) {
  const { locale, setAppLocale } = useLocaleContext();
  const t = (key: string) => tLocale(key, locale);

  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      await onPair(code);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('auth.pairing_failed'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center">
      <div className="bg-gray-900 rounded-xl p-8 w-full max-w-md border border-gray-800">
        <div className="text-center mb-6">
          <h1 className="text-2xl font-bold text-white mb-2">ZeroClaw</h1>
          <p className="text-gray-400">{t('auth.pairing_prompt')}</p>
        </div>
        <form onSubmit={handleSubmit}>
          <input
            type="text"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder={t('auth.code_placeholder')}
            className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white text-center text-2xl tracking-widest focus:outline-none focus:border-blue-500 mb-4"
            maxLength={6}
            autoFocus
          />
          {error && (
            <p className="text-red-400 text-sm mb-4 text-center">{error}</p>
          )}
          <button
            type="submit"
            disabled={loading || code.length < 6}
            className="w-full py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded-lg font-medium transition-colors"
          >
            {loading ? t('auth.pairing') : t('auth.pair_button')}
          </button>
        </form>

        <div className="flex justify-center mt-6">
          <select
            value={locale}
            onChange={(e) => setAppLocale(e.target.value as Locale)}
            className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500 cursor-pointer"
          >
            <option value="en">English</option>
            <option value="zh">中文</option>
            <option value="tr">Türkçe</option>
          </select>
        </div>
      </div>
    </div>
  );
}

function AppContent() {
  const { isAuthenticated, pair, logout } = useAuth();
  const storedLocale = loadStoredLocale();
  const initialLocale =
    storedLocale ??
    normalizeLocale(typeof navigator !== 'undefined' ? navigator.language : 'en') ??
    'en';
  const [locale, setLocaleState] = useState<Locale>(initialLocale);
  const [localeSource, setLocaleSource] = useState<'stored' | 'auto'>(
    storedLocale ? 'stored' : 'auto',
  );
  const [accessMode, setAccessMode] = useState<'unknown' | 'open' | 'pairing'>(
    isAuthenticated ? 'open' : 'unknown',
  );

  useEffect(() => {
    setLocale(locale);
  }, [locale]);

  const setAppLocale = (newLocale: Locale) => {
    setLocaleState(newLocale);
    setLocale(newLocale);
    saveStoredLocale(newLocale);
    setLocaleSource('stored');
  };

  // Listen for 401 events to force logout
  useEffect(() => {
    const handler = () => {
      logout();
      setAccessMode('pairing');
    };
    window.addEventListener('zeroclaw-unauthorized', handler);
    return () => window.removeEventListener('zeroclaw-unauthorized', handler);
  }, [logout]);

  useEffect(() => {
    if (isAuthenticated) {
      setAccessMode('open');
      return;
    }

    setAccessMode('unknown');

    fetch('/api/status')
      .then(async (res) => {
        if (res.ok) {
          setAccessMode('open');
          if (localeSource === 'auto') {
            const status = (await res.json()) as { locale?: string };
            const detected = normalizeLocale(status.locale) ?? 'en';
            setLocaleState(detected);
            setLocale(detected);
          }
          return;
        }

        if (res.status === 401) {
          setAccessMode('pairing');
          return;
        }

        setAccessMode('pairing');
      })
      .catch(() => {
        setAccessMode('pairing');
      });
  }, [isAuthenticated, localeSource]);

  useEffect(() => {
    if (!isAuthenticated) return;
    if (localeSource !== 'auto') return;

    getStatus()
      .then((status) => {
        const detected = normalizeLocale(status.locale) ?? 'en';
        setLocaleState(detected);
        setLocale(detected);
      })
      .catch(() => {
        // Ignore
      });
  }, [isAuthenticated, localeSource]);

  return (
    <LocaleContext.Provider value={{ locale, setAppLocale }}>
      {accessMode === 'unknown' ? (
        <div className="min-h-screen bg-gray-950 flex items-center justify-center">
          <div className="text-gray-400 text-sm">{tLocale('common.loading', locale)}</div>
        </div>
      ) : !isAuthenticated && accessMode === 'pairing' ? (
        <PairingDialog onPair={pair} />
      ) : (
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/agent" element={<AgentChat />} />
            <Route path="/tools" element={<Tools />} />
            <Route path="/cron" element={<Cron />} />
            <Route path="/integrations" element={<Integrations />} />
            <Route path="/memory" element={<Memory />} />
            <Route path="/config" element={<Config />} />
            <Route path="/cost" element={<Cost />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="/doctor" element={<Doctor />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      )}
    </LocaleContext.Provider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}
