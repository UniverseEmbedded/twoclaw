import { useLocation } from 'react-router-dom';
import { LogOut } from 'lucide-react';
import { tLocale, type Locale } from '@/lib/i18n';
import { useLocaleContext } from '@/App';
import { useAuth } from '@/hooks/useAuth';

const routeTitles: Record<string, string> = {
  '/': 'nav.dashboard',
  '/agent': 'nav.agent',
  '/tools': 'nav.tools',
  '/cron': 'nav.cron',
  '/integrations': 'nav.integrations',
  '/memory': 'nav.memory',
  '/config': 'nav.config',
  '/cost': 'nav.cost',
  '/logs': 'nav.logs',
  '/doctor': 'nav.doctor',
};

export default function Header() {
  const location = useLocation();
  const { isAuthenticated, logout } = useAuth();
  const { locale, setAppLocale } = useLocaleContext();
  const t = (key: string) => tLocale(key, locale);

  const titleKey = routeTitles[location.pathname] ?? 'nav.dashboard';
  const pageTitle = t(titleKey);

  return (
    <header className="h-14 bg-gray-800 border-b border-gray-700 flex items-center justify-between px-6">
      {/* Page title */}
      <h1 className="text-lg font-semibold text-white">{pageTitle}</h1>

      {/* Right-side controls */}
      <div className="flex items-center gap-4">
        {/* Language switcher */}
        <select
          value={locale}
          onChange={(e) => setAppLocale(e.target.value as Locale)}
          className="bg-gray-800 border border-gray-600 rounded-md px-3 py-1 text-sm text-gray-300 hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500 cursor-pointer"
        >
          <option value="en">English</option>
          <option value="zh">中文</option>
          <option value="tr">Türkçe</option>
        </select>

        {/* Logout */}
        {isAuthenticated && (
          <button
            type="button"
            onClick={logout}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm text-gray-300 hover:bg-gray-700 hover:text-white transition-colors"
          >
            <LogOut className="h-4 w-4" />
            <span>{t('auth.logout')}</span>
          </button>
        )}
      </div>
    </header>
  );
}
