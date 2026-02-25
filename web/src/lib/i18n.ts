import { useState, useEffect } from 'react';
import { getStatus } from './api';

// ---------------------------------------------------------------------------
// Translation dictionaries
// ---------------------------------------------------------------------------

export type Locale = 'en' | 'tr' | 'zh';

const translations: Record<Locale, Record<string, string>> = {
  en: {
    // Navigation
    'nav.dashboard': 'Dashboard',
    'nav.agent': 'Agent',
    'nav.tools': 'Tools',
    'nav.cron': 'Scheduled Jobs',
    'nav.integrations': 'Integrations',
    'nav.memory': 'Memory',
    'nav.config': 'Configuration',
    'nav.cost': 'Cost Tracker',
    'nav.logs': 'Logs',
    'nav.doctor': 'Doctor',

    // Dashboard
    'dashboard.title': 'Dashboard',
    'dashboard.provider': 'Provider',
    'dashboard.model': 'Model',
    'dashboard.uptime': 'Uptime',
    'dashboard.temperature': 'Temperature',
    'dashboard.gateway_port': 'Gateway Port',
    'dashboard.locale': 'Locale',
    'dashboard.memory_backend': 'Memory Backend',
    'dashboard.paired': 'Paired',
    'dashboard.channels': 'Channels',
    'dashboard.health': 'Health',
    'dashboard.status': 'Status',
    'dashboard.overview': 'Overview',
    'dashboard.system_info': 'System Information',
    'dashboard.quick_actions': 'Quick Actions',

    // Agent / Chat
    'agent.title': 'Agent Chat',
    'agent.send': 'Send',
    'agent.placeholder': 'Type a message...',
    'agent.connecting': 'Connecting...',
    'agent.connected': 'Connected',
    'agent.disconnected': 'Disconnected',
    'agent.reconnecting': 'Reconnecting...',
    'agent.thinking': 'Thinking...',
    'agent.tool_call': 'Tool Call',
    'agent.tool_result': 'Tool Result',

    // Tools
    'tools.title': 'Available Tools',
    'tools.name': 'Name',
    'tools.description': 'Description',
    'tools.parameters': 'Parameters',
    'tools.search': 'Search tools...',
    'tools.empty': 'No tools available.',
    'tools.count': 'Total tools',

    // Cron
    'cron.title': 'Scheduled Jobs',
    'cron.add': 'Add Job',
    'cron.delete': 'Delete',
    'cron.enable': 'Enable',
    'cron.disable': 'Disable',
    'cron.name': 'Name',
    'cron.command': 'Command',
    'cron.schedule': 'Schedule',
    'cron.next_run': 'Next Run',
    'cron.last_run': 'Last Run',
    'cron.last_status': 'Last Status',
    'cron.enabled': 'Enabled',
    'cron.empty': 'No scheduled jobs.',
    'cron.confirm_delete': 'Are you sure you want to delete this job?',

    // Integrations
    'integrations.title': 'Integrations',
    'integrations.load_failed': 'Failed to load integrations',
    'integrations.available': 'Available',
    'integrations.active': 'Active',
    'integrations.coming_soon': 'Coming Soon',
    'integrations.category': 'Category',
    'integrations.status': 'Status',
    'integrations.search': 'Search integrations...',
    'integrations.empty': 'No integrations found.',
    'integrations.activate': 'Activate',
    'integrations.deactivate': 'Deactivate',

    // Memory
    'memory.title': 'Memory Store',
    'memory.search': 'Search memory...',
    'memory.add': 'Store Memory',
    'memory.delete': 'Delete',
    'memory.key': 'Key',
    'memory.content': 'Content',
    'memory.category': 'Category',
    'memory.timestamp': 'Timestamp',
    'memory.session': 'Session',
    'memory.score': 'Score',
    'memory.empty': 'No memory entries found.',
    'memory.confirm_delete': 'Are you sure you want to delete this memory entry?',
    'memory.all_categories': 'All Categories',

    // Config
    'config.title': 'Configuration',
    'config.save': 'Save',
    'config.reset': 'Reset',
    'config.saved': 'Configuration saved successfully.',
    'config.error': 'Failed to save configuration.',
    'config.loading': 'Loading configuration...',
    'config.editor_placeholder': 'TOML configuration...',

    // Cost
    'cost.title': 'Cost Tracker',
    'cost.session': 'Session Cost',
    'cost.daily': 'Daily Cost',
    'cost.monthly': 'Monthly Cost',
    'cost.total_tokens': 'Total Tokens',
    'cost.request_count': 'Requests',
    'cost.by_model': 'Cost by Model',
    'cost.model': 'Model',
    'cost.tokens': 'Tokens',
    'cost.requests': 'Requests',
    'cost.usd': 'Cost (USD)',

    // Logs
    'logs.title': 'Live Logs',
    'logs.clear': 'Clear',
    'logs.pause': 'Pause',
    'logs.resume': 'Resume',
    'logs.filter': 'Filter logs...',
    'logs.empty': 'No log entries.',
    'logs.connected': 'Connected to event stream.',
    'logs.disconnected': 'Disconnected from event stream.',

    // Doctor
    'doctor.title': 'System Diagnostics',
    'doctor.run': 'Run Diagnostics',
    'doctor.running': 'Running diagnostics...',
    'doctor.ok': 'OK',
    'doctor.warn': 'Warning',
    'doctor.error': 'Error',
    'doctor.severity': 'Severity',
    'doctor.category': 'Category',
    'doctor.message': 'Message',
    'doctor.empty': 'No diagnostics have been run yet.',
    'doctor.summary': 'Diagnostic Summary',

    // Auth / Pairing
    'auth.pair': 'Pair Device',
    'auth.pairing_code': 'Pairing Code',
    'auth.pair_button': 'Pair',
    'auth.logout': 'Logout',
    'auth.pairing_success': 'Pairing successful!',
    'auth.pairing_failed': 'Pairing failed. Please try again.',
    'auth.enter_code': 'Enter your pairing code to connect to the agent.',

    // Common
    'common.loading': 'Loading...',
    'common.error': 'An error occurred.',
    'common.retry': 'Retry',
    'common.cancel': 'Cancel',
    'common.confirm': 'Confirm',
    'common.save': 'Save',
    'common.delete': 'Delete',
    'common.edit': 'Edit',
    'common.close': 'Close',
    'common.yes': 'Yes',
    'common.no': 'No',
    'common.search': 'Search...',
    'common.no_data': 'No data available.',
    'common.refresh': 'Refresh',
    'common.back': 'Back',
    'common.actions': 'Actions',
    'common.name': 'Name',
    'common.description': 'Description',
    'common.status': 'Status',
    'common.created': 'Created',
    'common.updated': 'Updated',
    'common.id': 'ID',
    'common.path': 'Path',
    'common.version': 'Version',
    'common.active': 'Active',
    'common.inactive': 'Inactive',
    'common.search_button': 'Search',
    'common.delete_question': 'Delete?',
    'common.saving': 'Saving...',

    // Health
    'health.title': 'System Health',
    'health.component': 'Component',
    'health.status': 'Status',
    'health.last_ok': 'Last OK',
    'health.last_error': 'Last Error',
    'health.restart_count': 'Restarts',
    'health.pid': 'Process ID',
    'health.uptime': 'Uptime',
    'health.updated_at': 'Last Updated',

    // App
    'app.language': 'Language',

    // Auth / Pairing (UI)
    'auth.pairing_prompt': 'Enter the pairing code from your terminal',
    'auth.code_placeholder': '6-digit code',
    'auth.pairing': 'Pairing...',

    // Dashboard (UI)
    'dashboard.load_failed': 'Failed to load dashboard',
    'dashboard.provider_model': 'Provider / Model',
    'dashboard.unknown': 'Unknown',
    'dashboard.since_last_restart': 'Since last restart',
    'dashboard.cost_overview': 'Cost Overview',
    'dashboard.channels_title': 'Channels',
    'dashboard.no_channels': 'No channels configured',
    'dashboard.component_health': 'Component Health',
    'dashboard.no_components_reporting': 'No components reporting',
    'dashboard.restarts': 'Restarts',

    // Agent (UI)
    'agent.empty_hint': 'Send a message to start the conversation',
    'agent.typing': 'Typing...',
    'agent.connection_error': 'Connection error. Attempting to reconnect...',
    'agent.send_failed': 'Failed to send message. Please try again.',

    // Tools (UI)
    'tools.load_failed': 'Failed to load tools',
    'tools.agent_tools': 'Agent Tools',
    'tools.cli_tools': 'CLI Tools',
    'tools.no_match': 'No tools match your search.',
    'tools.parameter_schema': 'Parameter Schema',

    // Cron (UI)
    'cron.load_failed': 'Failed to load cron jobs',
    'cron.tasks_title': 'Scheduled Tasks',
    'cron.add_title': 'Add Cron Job',
    'cron.name_optional': 'Name (optional)',
    'cron.name_placeholder': 'e.g. Daily cleanup',
    'cron.schedule_required': 'Schedule',
    'cron.schedule_placeholder': 'e.g. 0 0 * * * (cron expression)',
    'cron.command_required': 'Command',
    'cron.command_placeholder': 'e.g. cleanup --older-than 7d',
    'cron.validation_required': 'Schedule and command are required.',
    'cron.no_tasks': 'No scheduled tasks configured.',
    'cron.disabled': 'Disabled',
    'cron.delete_question': 'Delete?',
    'cron.adding': 'Adding...',

    // Memory (UI)
    'memory.load_failed': 'Failed to load memory',
    'memory.validation_required': 'Key and content are required.',
    'memory.search_placeholder': 'Search memory entries...',
    'memory.add_title': 'Add Memory',
    'memory.key_placeholder': 'e.g. user_preferences',
    'memory.content_placeholder': 'Memory content...',
    'memory.category_optional': 'Category (optional)',
    'memory.category_placeholder': 'e.g. preferences, context, facts',
    'memory.failed_store': 'Failed to store memory',
    'memory.failed_delete': 'Failed to delete memory',

    // Config (UI)
    'config.masked_title': 'Sensitive fields are masked',
    'config.masked_desc':
      'API keys, tokens, and passwords are hidden for security. To update a masked field, replace the entire masked value with your new value.',
    'config.editor_title': 'TOML Configuration',
    'config.lines': 'lines',
    'config.failed_save_fallback': 'Failed to save configuration',

    // Cost (UI)
    'cost.load_failed': 'Failed to load cost data',
    'cost.total_requests': 'Total Requests',
    'cost.token_stats': 'Token Statistics',
    'cost.avg_tokens_per_request': 'Avg Tokens / Request',
    'cost.cost_per_1k_tokens': 'Cost per 1K Tokens',
    'cost.model_breakdown': 'Model Breakdown',
    'cost.no_model_data': 'No model data available.',
    'cost.share': 'Share',

    // Logs (UI)
    'logs.events': 'events',
    'logs.jump_to_bottom': 'Jump to bottom',
    'logs.filter_label': 'Filter:',
    'logs.waiting': 'Waiting for events...',
    'logs.paused_stream': 'Log streaming is paused.',

    // Doctor (UI)
    'doctor.title_short': 'Diagnostics',
    'doctor.running_hint': 'This may take a few seconds.',
    'doctor.failed_run_fallback': 'Failed to run diagnostics',
    'doctor.issues_found': 'Issues Found',
    'doctor.warnings': 'Warnings',
    'doctor.all_clear': 'All Clear',
  },

  tr: {
    // Navigation
    'nav.dashboard': 'Kontrol Paneli',
    'nav.agent': 'Ajan',
    'nav.tools': 'Araclar',
    'nav.cron': 'Zamanlanmis Gorevler',
    'nav.integrations': 'Entegrasyonlar',
    'nav.memory': 'Hafiza',
    'nav.config': 'Yapilandirma',
    'nav.cost': 'Maliyet Takibi',
    'nav.logs': 'Kayitlar',
    'nav.doctor': 'Doktor',

    // Dashboard
    'dashboard.title': 'Kontrol Paneli',
    'dashboard.provider': 'Saglayici',
    'dashboard.model': 'Model',
    'dashboard.uptime': 'Calisma Suresi',
    'dashboard.temperature': 'Sicaklik',
    'dashboard.gateway_port': 'Gecit Portu',
    'dashboard.locale': 'Yerel Ayar',
    'dashboard.memory_backend': 'Hafiza Motoru',
    'dashboard.paired': 'Eslestirilmis',
    'dashboard.channels': 'Kanallar',
    'dashboard.health': 'Saglik',
    'dashboard.status': 'Durum',
    'dashboard.overview': 'Genel Bakis',
    'dashboard.system_info': 'Sistem Bilgisi',
    'dashboard.quick_actions': 'Hizli Islemler',

    // Agent / Chat
    'agent.title': 'Ajan Sohbet',
    'agent.send': 'Gonder',
    'agent.placeholder': 'Bir mesaj yazin...',
    'agent.connecting': 'Baglaniyor...',
    'agent.connected': 'Bagli',
    'agent.disconnected': 'Baglanti Kesildi',
    'agent.reconnecting': 'Yeniden Baglaniyor...',
    'agent.thinking': 'Dusunuyor...',
    'agent.tool_call': 'Arac Cagrisi',
    'agent.tool_result': 'Arac Sonucu',

    // Tools
    'tools.title': 'Mevcut Araclar',
    'tools.name': 'Ad',
    'tools.description': 'Aciklama',
    'tools.parameters': 'Parametreler',
    'tools.search': 'Arac ara...',
    'tools.empty': 'Mevcut arac yok.',
    'tools.count': 'Toplam arac',

    // Cron
    'cron.title': 'Zamanlanmis Gorevler',
    'cron.add': 'Gorev Ekle',
    'cron.delete': 'Sil',
    'cron.enable': 'Etkinlestir',
    'cron.disable': 'Devre Disi Birak',
    'cron.name': 'Ad',
    'cron.command': 'Komut',
    'cron.schedule': 'Zamanlama',
    'cron.next_run': 'Sonraki Calistirma',
    'cron.last_run': 'Son Calistirma',
    'cron.last_status': 'Son Durum',
    'cron.enabled': 'Etkin',
    'cron.empty': 'Zamanlanmis gorev yok.',
    'cron.confirm_delete': 'Bu gorevi silmek istediginizden emin misiniz?',

    // Integrations
    'integrations.title': 'Entegrasyonlar',
    'integrations.load_failed': 'Entegrasyonlar yuklenemedi',
    'integrations.available': 'Mevcut',
    'integrations.active': 'Aktif',
    'integrations.coming_soon': 'Yakinda',
    'integrations.category': 'Kategori',
    'integrations.status': 'Durum',
    'integrations.search': 'Entegrasyon ara...',
    'integrations.empty': 'Entegrasyon bulunamadi.',
    'integrations.activate': 'Etkinlestir',
    'integrations.deactivate': 'Devre Disi Birak',

    // Memory
    'memory.title': 'Hafiza Deposu',
    'memory.search': 'Hafizada ara...',
    'memory.add': 'Hafiza Kaydet',
    'memory.delete': 'Sil',
    'memory.key': 'Anahtar',
    'memory.content': 'Icerik',
    'memory.category': 'Kategori',
    'memory.timestamp': 'Zaman Damgasi',
    'memory.session': 'Oturum',
    'memory.score': 'Skor',
    'memory.empty': 'Hafiza kaydi bulunamadi.',
    'memory.confirm_delete': 'Bu hafiza kaydini silmek istediginizden emin misiniz?',
    'memory.all_categories': 'Tum Kategoriler',

    // Config
    'config.title': 'Yapilandirma',
    'config.save': 'Kaydet',
    'config.reset': 'Sifirla',
    'config.saved': 'Yapilandirma basariyla kaydedildi.',
    'config.error': 'Yapilandirma kaydedilemedi.',
    'config.loading': 'Yapilandirma yukleniyor...',
    'config.editor_placeholder': 'TOML yapilandirmasi...',

    // Cost
    'cost.title': 'Maliyet Takibi',
    'cost.session': 'Oturum Maliyeti',
    'cost.daily': 'Gunluk Maliyet',
    'cost.monthly': 'Aylik Maliyet',
    'cost.total_tokens': 'Toplam Token',
    'cost.request_count': 'Istekler',
    'cost.by_model': 'Modele Gore Maliyet',
    'cost.model': 'Model',
    'cost.tokens': 'Token',
    'cost.requests': 'Istekler',
    'cost.usd': 'Maliyet (USD)',

    // Logs
    'logs.title': 'Canli Kayitlar',
    'logs.clear': 'Temizle',
    'logs.pause': 'Duraklat',
    'logs.resume': 'Devam Et',
    'logs.filter': 'Kayitlari filtrele...',
    'logs.empty': 'Kayit girisi yok.',
    'logs.connected': 'Olay akisina baglandi.',
    'logs.disconnected': 'Olay akisi baglantisi kesildi.',

    // Doctor
    'doctor.title': 'Sistem Teshisleri',
    'doctor.run': 'Teshis Calistir',
    'doctor.running': 'Teshisler calistiriliyor...',
    'doctor.ok': 'Tamam',
    'doctor.warn': 'Uyari',
    'doctor.error': 'Hata',
    'doctor.severity': 'Ciddiyet',
    'doctor.category': 'Kategori',
    'doctor.message': 'Mesaj',
    'doctor.empty': 'Henuz teshis calistirilmadi.',
    'doctor.summary': 'Teshis Ozeti',

    // Auth / Pairing
    'auth.pair': 'Cihaz Esle',
    'auth.pairing_code': 'Eslestirme Kodu',
    'auth.pair_button': 'Esle',
    'auth.logout': 'Cikis Yap',
    'auth.pairing_success': 'Eslestirme basarili!',
    'auth.pairing_failed': 'Eslestirme basarisiz. Lutfen tekrar deneyin.',
    'auth.enter_code': 'Ajana baglanmak icin eslestirme kodunuzu girin.',

    // Common
    'common.loading': 'Yukleniyor...',
    'common.error': 'Bir hata olustu.',
    'common.retry': 'Tekrar Dene',
    'common.cancel': 'Iptal',
    'common.confirm': 'Onayla',
    'common.save': 'Kaydet',
    'common.delete': 'Sil',
    'common.edit': 'Duzenle',
    'common.close': 'Kapat',
    'common.yes': 'Evet',
    'common.no': 'Hayir',
    'common.search': 'Ara...',
    'common.no_data': 'Veri mevcut degil.',
    'common.refresh': 'Yenile',
    'common.back': 'Geri',
    'common.actions': 'Islemler',
    'common.name': 'Ad',
    'common.description': 'Aciklama',
    'common.status': 'Durum',
    'common.created': 'Olusturulma',
    'common.updated': 'Guncellenme',
    'common.id': 'ID',
    'common.path': 'Yol',
    'common.version': 'Surum',
    'common.active': 'Aktif',
    'common.inactive': 'Pasif',
    'common.search_button': 'Ara',
    'common.delete_question': 'Sil?',
    'common.saving': 'Kaydediliyor...',

    // Health
    'health.title': 'Sistem Sagligi',
    'health.component': 'Bilesen',
    'health.status': 'Durum',
    'health.last_ok': 'Son Basarili',
    'health.last_error': 'Son Hata',
    'health.restart_count': 'Yeniden Baslatmalar',
    'health.pid': 'Islem Kimligi',
    'health.uptime': 'Calisma Suresi',
    'health.updated_at': 'Son Guncelleme',

    // App
    'app.language': 'Dil',

    // Auth / Pairing (UI)
    'auth.pairing_prompt': 'Terminalinizden eslestirme kodunu girin',
    'auth.code_placeholder': '6 haneli kod',
    'auth.pairing': 'Eslesiyor...',

    // Dashboard (UI)
    'dashboard.load_failed': 'Kontrol paneli yuklenemedi',
    'dashboard.provider_model': 'Saglayici / Model',
    'dashboard.unknown': 'Bilinmiyor',
    'dashboard.since_last_restart': 'Son yeniden baslatmadan beri',
    'dashboard.cost_overview': 'Maliyet Ozeti',
    'dashboard.channels_title': 'Kanallar',
    'dashboard.no_channels': 'Kanal yapilandirilamadi',
    'dashboard.component_health': 'Bilesen Sagligi',
    'dashboard.no_components_reporting': 'Bilesen raporu yok',
    'dashboard.restarts': 'Yeniden Baslatmalar',

    // Agent (UI)
    'agent.empty_hint': 'Sohbeti baslatmak icin mesaj gonderin',
    'agent.typing': 'Yaziyor...',
    'agent.connection_error': 'Baglanti hatasi. Yeniden baglaniliyor...',
    'agent.send_failed': 'Mesaj gonderilemedi. Lutfen tekrar deneyin.',

    // Tools (UI)
    'tools.load_failed': 'Araclar yuklenemedi',
    'tools.agent_tools': 'Ajan Araclari',
    'tools.cli_tools': 'CLI Araclari',
    'tools.no_match': 'Aramanizla eslesen arac yok.',
    'tools.parameter_schema': 'Parametre Semasi',

    // Cron (UI)
    'cron.load_failed': 'Zamanlanmis gorevler yuklenemedi',
    'cron.tasks_title': 'Zamanlanmis Gorevler',
    'cron.add_title': 'Cron Gorevi Ekle',
    'cron.name_optional': 'Ad (istege bagli)',
    'cron.name_placeholder': 'or. Gunluk temizlik',
    'cron.schedule_required': 'Zamanlama',
    'cron.schedule_placeholder': 'or. 0 0 * * * (cron ifadesi)',
    'cron.command_required': 'Komut',
    'cron.command_placeholder': 'or. cleanup --older-than 7d',
    'cron.validation_required': 'Zamanlama ve komut gereklidir.',
    'cron.no_tasks': 'Zamanlanmis gorev yok.',
    'cron.disabled': 'Devre Disi',
    'cron.delete_question': 'Sil?',
    'cron.adding': 'Ekleniyor...',

    // Memory (UI)
    'memory.load_failed': 'Hafiza yuklenemedi',
    'memory.validation_required': 'Anahtar ve icerik gereklidir.',
    'memory.search_placeholder': 'Hafiza kayitlarinda ara...',
    'memory.add_title': 'Hafiza Ekle',
    'memory.key_placeholder': 'or. user_preferences',
    'memory.content_placeholder': 'Hafiza icerigi...',
    'memory.category_optional': 'Kategori (istege bagli)',
    'memory.category_placeholder': 'or. preferences, context, facts',
    'memory.failed_store': 'Hafiza kaydedilemedi',
    'memory.failed_delete': 'Hafiza silinemedi',

    // Config (UI)
    'config.masked_title': 'Hassas alanlar maskelenir',
    'config.masked_desc':
      'API anahtarlari, tokenlar ve parolalar guvenlik icin gizlenir. Maskeli bir alanı guncellemek icin, maskeli degeri tamamen yeni degerinizle degistirin.',
    'config.editor_title': 'TOML Yapilandirmasi',
    'config.lines': 'satir',
    'config.failed_save_fallback': 'Yapilandirma kaydedilemedi',

    // Cost (UI)
    'cost.load_failed': 'Maliyet verileri yuklenemedi',
    'cost.total_requests': 'Toplam Istek',
    'cost.token_stats': 'Token Istatistikleri',
    'cost.avg_tokens_per_request': 'Istek Basina Ortalama Token',
    'cost.cost_per_1k_tokens': '1K Token Basina Maliyet',
    'cost.model_breakdown': 'Model Dagilimi',
    'cost.no_model_data': 'Model verisi yok.',
    'cost.share': 'Pay',

    // Logs (UI)
    'logs.events': 'olay',
    'logs.jump_to_bottom': 'Alta git',
    'logs.filter_label': 'Filtre:',
    'logs.waiting': 'Olaylar bekleniyor...',
    'logs.paused_stream': 'Kayit akisi duraklatildi.',

    // Doctor (UI)
    'doctor.title_short': 'Teshisler',
    'doctor.running_hint': 'Bu islem birkac saniye surebilir.',
    'doctor.failed_run_fallback': 'Teshisler calistirilamadi',
    'doctor.issues_found': 'Sorun Bulundu',
    'doctor.warnings': 'Uyari',
    'doctor.all_clear': 'Her sey yolunda',
  },

  zh: {
    // Navigation
    'nav.dashboard': '仪表盘',
    'nav.agent': '智能体',
    'nav.tools': '工具',
    'nav.cron': '定时任务',
    'nav.integrations': '集成',
    'nav.memory': '记忆',
    'nav.config': '配置',
    'nav.cost': '成本',
    'nav.logs': '日志',
    'nav.doctor': '诊断',

    // Dashboard
    'dashboard.title': '仪表盘',
    'dashboard.provider': '提供方',
    'dashboard.model': '模型',
    'dashboard.uptime': '运行时间',
    'dashboard.temperature': '温度',
    'dashboard.gateway_port': '网关端口',
    'dashboard.locale': '语言',
    'dashboard.memory_backend': '记忆后端',
    'dashboard.paired': '已配对',
    'dashboard.channels': '渠道',
    'dashboard.health': '健康',
    'dashboard.status': '状态',
    'dashboard.overview': '概览',
    'dashboard.system_info': '系统信息',
    'dashboard.quick_actions': '快捷操作',

    // Agent / Chat
    'agent.title': '智能体聊天',
    'agent.send': '发送',
    'agent.placeholder': '输入消息...',
    'agent.connecting': '连接中...',
    'agent.connected': '已连接',
    'agent.disconnected': '未连接',
    'agent.reconnecting': '重新连接中...',
    'agent.thinking': '思考中...',
    'agent.tool_call': '工具调用',
    'agent.tool_result': '工具结果',

    // Tools
    'tools.title': '可用工具',
    'tools.name': '名称',
    'tools.description': '描述',
    'tools.parameters': '参数',
    'tools.search': '搜索工具...',
    'tools.empty': '暂无可用工具。',
    'tools.count': '工具总数',

    // Cron
    'cron.title': '定时任务',
    'cron.add': '添加任务',
    'cron.delete': '删除',
    'cron.enable': '启用',
    'cron.disable': '禁用',
    'cron.name': '名称',
    'cron.command': '命令',
    'cron.schedule': '计划',
    'cron.next_run': '下次运行',
    'cron.last_run': '上次运行',
    'cron.last_status': '上次状态',
    'cron.enabled': '已启用',
    'cron.empty': '暂无定时任务。',
    'cron.confirm_delete': '确定要删除该任务吗？',

    // Integrations
    'integrations.title': '集成',
    'integrations.load_failed': '集成加载失败',
    'integrations.available': '可用',
    'integrations.active': '已启用',
    'integrations.coming_soon': '即将推出',
    'integrations.category': '分类',
    'integrations.status': '状态',
    'integrations.search': '搜索集成...',
    'integrations.empty': '未找到集成。',
    'integrations.activate': '启用',
    'integrations.deactivate': '停用',

    // Memory
    'memory.title': '记忆库',
    'memory.search': '搜索记忆...',
    'memory.add': '新增记忆',
    'memory.delete': '删除',
    'memory.key': '键',
    'memory.content': '内容',
    'memory.category': '分类',
    'memory.timestamp': '时间',
    'memory.session': '会话',
    'memory.score': '得分',
    'memory.empty': '暂无记忆条目。',
    'memory.confirm_delete': '确定要删除该记忆条目吗？',
    'memory.all_categories': '所有分类',

    // Config
    'config.title': '配置',
    'config.save': '保存',
    'config.reset': '重置',
    'config.saved': '配置保存成功。',
    'config.error': '配置保存失败。',
    'config.loading': '正在加载配置...',
    'config.editor_placeholder': 'TOML 配置...',

    // Cost
    'cost.title': '成本',
    'cost.session': '会话成本',
    'cost.daily': '日成本',
    'cost.monthly': '月成本',
    'cost.total_tokens': '总 Token',
    'cost.request_count': '请求数',
    'cost.by_model': '按模型统计',
    'cost.model': '模型',
    'cost.tokens': 'Token',
    'cost.requests': '请求数',
    'cost.usd': '成本 (USD)',

    // Logs
    'logs.title': '实时日志',
    'logs.clear': '清空',
    'logs.pause': '暂停',
    'logs.resume': '继续',
    'logs.filter': '过滤日志...',
    'logs.empty': '暂无日志。',
    'logs.connected': '已连接到事件流。',
    'logs.disconnected': '事件流已断开。',

    // Doctor
    'doctor.title': '系统诊断',
    'doctor.run': '运行诊断',
    'doctor.running': '正在运行诊断...',
    'doctor.ok': '正常',
    'doctor.warn': '警告',
    'doctor.error': '错误',
    'doctor.severity': '级别',
    'doctor.category': '分类',
    'doctor.message': '消息',
    'doctor.empty': '尚未运行诊断。',
    'doctor.summary': '诊断汇总',

    // Auth / Pairing
    'auth.pair': '配对设备',
    'auth.pairing_code': '配对码',
    'auth.pair_button': '配对',
    'auth.logout': '退出登录',
    'auth.pairing_success': '配对成功！',
    'auth.pairing_failed': '配对失败，请重试。',
    'auth.enter_code': '输入配对码以连接智能体。',

    // Common
    'common.loading': '加载中...',
    'common.error': '发生错误。',
    'common.retry': '重试',
    'common.cancel': '取消',
    'common.confirm': '确认',
    'common.save': '保存',
    'common.delete': '删除',
    'common.edit': '编辑',
    'common.close': '关闭',
    'common.yes': '是',
    'common.no': '否',
    'common.search': '搜索...',
    'common.no_data': '暂无数据。',
    'common.refresh': '刷新',
    'common.back': '返回',
    'common.actions': '操作',
    'common.name': '名称',
    'common.description': '描述',
    'common.status': '状态',
    'common.created': '创建时间',
    'common.updated': '更新时间',
    'common.id': 'ID',
    'common.path': '路径',
    'common.version': '版本',
    'common.active': '启用',
    'common.inactive': '停用',
    'common.search_button': '搜索',
    'common.delete_question': '删除？',
    'common.saving': '保存中...',

    // Health
    'health.title': '系统健康',
    'health.component': '组件',
    'health.status': '状态',
    'health.last_ok': '上次正常',
    'health.last_error': '上次错误',
    'health.restart_count': '重启次数',
    'health.pid': '进程 ID',
    'health.uptime': '运行时间',
    'health.updated_at': '更新时间',

    // App
    'app.language': '语言',

    // Auth / Pairing (UI)
    'auth.pairing_prompt': '请输入终端中显示的配对码',
    'auth.code_placeholder': '6 位数字',
    'auth.pairing': '正在配对...',

    // Dashboard (UI)
    'dashboard.load_failed': '仪表盘加载失败',
    'dashboard.provider_model': '提供方 / 模型',
    'dashboard.unknown': '未知',
    'dashboard.since_last_restart': '自上次重启以来',
    'dashboard.cost_overview': '成本概览',
    'dashboard.channels_title': '渠道',
    'dashboard.no_channels': '尚未配置渠道',
    'dashboard.component_health': '组件健康',
    'dashboard.no_components_reporting': '暂无组件上报',
    'dashboard.restarts': '重启次数',

    // Agent (UI)
    'agent.empty_hint': '发送消息开始对话',
    'agent.typing': '正在输入...',
    'agent.connection_error': '连接异常，正在尝试重连...',
    'agent.send_failed': '消息发送失败，请重试。',

    // Tools (UI)
    'tools.load_failed': '工具加载失败',
    'tools.agent_tools': '智能体工具',
    'tools.cli_tools': 'CLI 工具',
    'tools.no_match': '没有符合搜索条件的工具。',
    'tools.parameter_schema': '参数结构',

    // Cron (UI)
    'cron.load_failed': '定时任务加载失败',
    'cron.tasks_title': '定时任务',
    'cron.add_title': '添加定时任务',
    'cron.name_optional': '名称（可选）',
    'cron.name_placeholder': '例如：每日清理',
    'cron.schedule_required': '计划',
    'cron.schedule_placeholder': '例如：0 0 * * *（cron 表达式）',
    'cron.command_required': '命令',
    'cron.command_placeholder': '例如：cleanup --older-than 7d',
    'cron.validation_required': '计划与命令为必填项。',
    'cron.no_tasks': '尚未配置定时任务。',
    'cron.disabled': '已禁用',
    'cron.delete_question': '删除？',
    'cron.adding': '添加中...',

    // Memory (UI)
    'memory.load_failed': '记忆加载失败',
    'memory.validation_required': '键与内容为必填项。',
    'memory.search_placeholder': '搜索记忆条目...',
    'memory.add_title': '新增记忆',
    'memory.key_placeholder': '例如：user_preferences',
    'memory.content_placeholder': '记忆内容...',
    'memory.category_optional': '分类（可选）',
    'memory.category_placeholder': '例如：preferences、context、facts',
    'memory.failed_store': '记忆保存失败',
    'memory.failed_delete': '记忆删除失败',

    // Config (UI)
    'config.masked_title': '敏感字段已被遮罩',
    'config.masked_desc':
      '为保障安全，API Key、Token 与密码会被隐藏。若要更新被遮罩的字段，请用新值替换整个被遮罩的值。',
    'config.editor_title': 'TOML 配置',
    'config.lines': '行',
    'config.failed_save_fallback': '配置保存失败',

    // Cost (UI)
    'cost.load_failed': '成本数据加载失败',
    'cost.total_requests': '总请求数',
    'cost.token_stats': 'Token 统计',
    'cost.avg_tokens_per_request': '平均 Token / 请求',
    'cost.cost_per_1k_tokens': '每 1K Token 成本',
    'cost.model_breakdown': '模型明细',
    'cost.no_model_data': '暂无模型数据。',
    'cost.share': '占比',

    // Logs (UI)
    'logs.events': '条事件',
    'logs.jump_to_bottom': '跳到底部',
    'logs.filter_label': '过滤：',
    'logs.waiting': '等待事件...',
    'logs.paused_stream': '日志流已暂停。',

    // Doctor (UI)
    'doctor.title_short': '诊断',
    'doctor.running_hint': '这可能需要几秒钟。',
    'doctor.failed_run_fallback': '诊断运行失败',
    'doctor.issues_found': '发现问题',
    'doctor.warnings': '警告',
    'doctor.all_clear': '一切正常',
  },
};

// ---------------------------------------------------------------------------
// Current locale state
// ---------------------------------------------------------------------------

let currentLocale: Locale = 'en';

export function getLocale(): Locale {
  return currentLocale;
}

export function setLocale(locale: Locale): void {
  currentLocale = locale;
}

// ---------------------------------------------------------------------------
// Translation function
// ---------------------------------------------------------------------------

/**
 * Translate a key using the current locale. Returns the key itself if no
 * translation is found.
 */
export function t(key: string): string {
  return translations[currentLocale]?.[key] ?? translations.en[key] ?? key;
}

/**
 * Get the translation for a specific locale. Falls back to English, then to the
 * raw key.
 */
export function tLocale(key: string, locale: Locale): string {
  return translations[locale]?.[key] ?? translations.en[key] ?? key;
}

// ---------------------------------------------------------------------------
// React hook
// ---------------------------------------------------------------------------

/**
 * React hook that fetches the locale from /api/status on mount and keeps the
 * i18n module in sync. Returns the current locale and a `t` helper bound to it.
 */
export function useLocale(): { locale: Locale; t: (key: string) => string } {
  const [locale, setLocaleState] = useState<Locale>(currentLocale);

  useEffect(() => {
    let cancelled = false;

    getStatus()
      .then((status) => {
        if (cancelled) return;
        const localeRaw = status.locale?.toLowerCase() ?? '';
        const detected: Locale = localeRaw.startsWith('zh')
          ? 'zh'
          : localeRaw.startsWith('tr')
            ? 'tr'
            : 'en';
        setLocale(detected);
        setLocaleState(detected);
      })
      .catch(() => {
        // Keep default locale on error
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return {
    locale,
    t: (key: string) => tLocale(key, locale),
  };
}
