/**
 * Cloudflare Worker — прозрачный прокси к Telegram Bot API.
 *
 * Зачем: Hugging Face Spaces по политике безопасности блокирует исходящие
 * запросы к api.telegram.org. Бот на HF шлёт запросы сюда (через base_url),
 * а Worker форвардит их в Telegram — egress-блок обходится.
 *
 * Деплой (бесплатно, без карты):
 *   1. dash.cloudflare.com → Workers & Pages → Create → Create Worker
 *   2. Назови, напр. jarvis-tg-proxy → Deploy
 *   3. Edit code → вставь ВЕСЬ этот файл → Deploy
 *   4. Скопируй URL вида https://jarvis-tg-proxy.<твой-сабдомен>.workers.dev
 *
 * Пробрасывает и /bot<token>/<method>, и /file/bot<token>/<path> (файлы:
 * голосовые, фото). Всё остальное отклоняет, чтобы прокси не юзали посторонние.
 */
export default {
  async fetch(request) {
    const url = new URL(request.url);

    // Пропускаем только реальные пути Telegram API.
    if (!url.pathname.startsWith("/bot") && !url.pathname.startsWith("/file/bot")) {
      return new Response("Not found", { status: 404 });
    }

    const target = "https://api.telegram.org" + url.pathname + url.search;

    const headers = new Headers(request.headers);
    headers.delete("host"); // пусть fetch сам выставит Host: api.telegram.org

    const isBodyless = request.method === "GET" || request.method === "HEAD";

    const resp = await fetch(target, {
      method: request.method,
      headers,
      body: isBodyless ? undefined : request.body,
    });

    // Возвращаем ответ Telegram как есть.
    return new Response(resp.body, {
      status: resp.status,
      statusText: resp.statusText,
      headers: resp.headers,
    });
  },
};
