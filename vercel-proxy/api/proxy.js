/**
 * Прокси Telegram Bot API на Vercel Edge.
 *
 * Зачем: Hugging Face Spaces точечно блокирует исходящие соединения к
 * api.telegram.org И к *.workers.dev (проверено 05.08.2026: ConnectTimeout к
 * обоим, при этом vercel.com/deno.com/pypi.org из того же контейнера
 * доступны). Cloudflare-воркер, работавший до 21 июля, стал недостижим —
 * этот прокси его заменяет.
 *
 * Деплой:
 *   cd vercel-proxy && vercel deploy --prod
 * Затем полученный URL прописать в Space как секрет TELEGRAM_API_BASE.
 *
 * Пропускает только настоящие пути Telegram API (/bot… и /file/bot…), чтобы
 * прокси не использовали посторонние. Токен в пути не логируется.
 */
export const config = { runtime: 'edge' };

const TELEGRAM = 'https://api.telegram.org';

export default async function handler(request) {
  const url = new URL(request.url);

  if (!url.pathname.startsWith('/bot') && !url.pathname.startsWith('/file/bot')) {
    return new Response('Not found', { status: 404 });
  }

  const headers = new Headers(request.headers);
  // Пусть fetch сам выставит Host: api.telegram.org, иначе Telegram ответит 421.
  headers.delete('host');

  const isBodyless = request.method === 'GET' || request.method === 'HEAD';

  const upstream = await fetch(TELEGRAM + url.pathname + url.search, {
    method: request.method,
    headers,
    body: isBodyless ? undefined : request.body,
    // Стриминг тела нужен для отправки голосовых и фото без буферизации.
    duplex: isBodyless ? undefined : 'half',
    redirect: 'manual',
  });

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: upstream.headers,
  });
}
