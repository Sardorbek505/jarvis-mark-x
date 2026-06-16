# UX/UI Typography Rules (DesignMe / Adrian K) — применять к Mini App

Источник: серия гайдов «font sizes & weights» для fintech/SaaS мобильных UI.
Цель: чёткая иерархия, премиальный вид, читаемость на маленьких размерах.

## Шкала размеров (pt ≈ px на мобиле)
| Роль | Размер | Вес | Заметки |
|---|---|---|---|
| Large page title (импакт) | 24–34pt | bold (700–800) | 34 только для редких акцентов, обычно 24–28 |
| Centered title (в шапке) | 14–17pt | medium–semibold | 17 заметнее, 14–15 утончённее |
| Primary text (body, поля, пункты меню, модалки) | 14–18pt | regular/medium | основной текст |
| List item title | 14–15pt | regular–medium | заголовок строки списка |
| List item description | 12–13pt | regular–medium | чуть светлее контраст |
| Secondary text (под primary) | 12–14pt | regular | светлее контраст |
| Tertiary (сноски, подписи, таймстампы) | 11–13pt | regular–medium | приглушённый тон |
| Min text / tab bar labels | 10–12pt | regular | пол; цвет достаточно контрастный |
| Main CTA text | 15–17pt | medium/semibold/bold | tap target ≥ 44×44pt |
| Secondary action button | 13–14pt | medium | компактная кнопка = сама tap target |

## Принципы
- Чем критичнее действие — тем крупнее кнопка/текст.
- НЕ делать всё одним размером и весом → иначе ничего не «важное».
- Иерархия: один крупный заголовок → primary → secondary/tertiary приглушённо.
- Контраст: приглушённый текст должен оставаться читаемым (на тёмном фоне — светлее, не уходить в грязно-серый).
- Tap target ≥ 44px даже если визуально кнопка меньше.

## Маппинг на наш Mini App
- Приветствие в Сводке = large title 25px/800.
- Заголовок экрана в шапке = 17px/700.
- Заголовки строк (задачи/привычки) = 15px/medium; мета/время = 12–13px muted.
- Подписи карточек/секций = 11–12px label.
- Чат-пузыри = 14–15px primary.
- Лейбл под орбом, подписи = 11–12px muted.
- Кнопки-плитки ПК = label 11px/600; tap target крупный.
