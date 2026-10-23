# 02 — Рынок, аналоги, цифры для «Открой Моспром» (ПОЛИПЛАСТИК)

Разведка 23.10.2026, около 25 минут, только открытые веб-источники.
Пометки: **[вендор]** — цифра от продавца без методики; **[вторичка]** — первоисточник не найден, есть только пересказы; **[не подтв.]** — проверить до слайда.

---

## TL;DR

1. **Рынок РФ раздроблен по функциям.** Онбординг-платформы (Поток, Skillaz, HRChain, Motivity, Mirapolis) делают треки и геймификацию, а ИИ в них в основном для **подбора**. HR-боты (Personik, конструкторы вроде Botmother) отвечают по базе знаний, но **sentiment-аналитики опросов, эскалации «красных флагов» и нуджей руководителю ни у кого из найденных российских вендоров нет**. Единственный прямой конкурент по ИИ — Сбер «Пульс» (ИИ-агент на GigaChat закрывает 86% обращений), но его агент работает как поддержка платформы, а не как наставник новичка.
2. **МАКС — свободная ниша.** Из найденных HR-решений подтверждённой интеграции с МАКС нет ни у одного. МАКС поддерживают только конструкторы общего назначения (Botmother, Unisender). Аудитория МАКС: DAU 18,2 млн в октябре 2025 и больше 77 млн в марте 2026 (по сообщениям СМИ).
3. **Зарубежные лидеры разделили задачу на три продукта**, и мы собираем все три в одном боте: Leena AI / Moveworks (ответы, 54–88% закрытия без человека [вендор]), Peakon / Glint / Culture Amp (ИИ-разбор комментариев опросов, прогноз ухода), Enboarder / Humu (нуджи руководителю; у Enboarder текучесть за 90 дней снижена на 36% [вендор]).
4. **Главная цифра «боли» из РФ:** 40% новичков думали уволиться в первые месяцы, 58% из них назвали причиной расхождение ожиданий с реальностью; в 59% мест работы онбординга нет (hh.ru + Edstein, 2024). У 22% работодателей до 30% новичков уходят в первые 3 месяца (hh.ru, 2025).
5. **Ключевая цифра Gallup:** только 12% сотрудников уверены, что их компания хорошо проводит онбординг. Если руководитель активно участвует, вероятность отличного онбординга в **3,4 раза выше**. Это прямое обоснование функции «пуш руководителю: проведи встречу».
6. **Предпочтения новичков работают против «голого бота»:** 72% хотят наставничество, 49% — личные встречи, и только 14% — приложение в телефоне (hh.ru + Edstein, 2024). Поэтому бот надо подавать как **инструмент, который подталкивает людей к людям**, а не как замену HR.
7. **Сегмент рабочих без ПК — главный аргумент для производства.** Норникель сделал чат-бот адаптации именно для рабочих без компьютера: в первый год им пользовались больше 2000 человек, из них 600 рабочих (2021). Мессенджер в телефоне — единственный канал, который есть у сменного рабочего.
8. **Потенциал тиражирования:** в Москве 4,5 тыс. промышленных предприятий и больше 750 тыс. занятых, к 2030 году ожидается 850 тыс. (mos.ru). У самого ПОЛИПЛАСТИКа 33 завода в РФ, РБ и РК (hh.ru).

---

## 1. Таблица аналогов

### 1.1. Россия

| Решение | Что делает | ИИ-фичи | Мессенджеры / МАКС | Цифры / цена | Ссылка |
|---|---|---|---|---|---|
| **Сбер «Пульс»** | HR-платформа полного цикла, больше 40 приложений «в одном окне» | ИИ-агент поддержки на GigaChat: помнит контекст, проверяет статус заявок. GigaChat генерирует учебные тексты | Своё приложение/веб; МАКС не упоминается | **86% вопросов закрываются без техподдержки**; больше 50 компаний-клиентов (09.2026) [вендор] | [РИАМО](https://riamo.ru/news/obschestvo/ii-agent-sbera-vnutri-hr-platformy-puls-86-voprosov-reshajutsja-agentom/) |
| **Personik** | HR-бот: «личный кабинет в мессенджере», сценарии онбординга, ответы по документам, перевод на HR или наставника | Частично: база знаний отвечает на вопросы | Публичные и корпоративные мессенджеры; поддержка МАКС **не подтверждена** | от 150 000 ₽/год; есть в реестре ПО | [toolfox](https://toolfox.ru/services/s/personik-ai), [soware](https://soware.ru/products/personik), [ruward](https://ruward.ru/catalog/personik-ai/) |
| **Поток (Potok)** | ATS + модуль «Поток Адаптация» с геймификацией | ИИ-рекрутер, скоринг резюме, YandexGPT для вакансий. **ИИ в адаптации не заявлен** | Чат- и голосовой бот для кандидатов | от 2 000 ₽/польз./мес | [toolfox](https://toolfox.ru/services/hr-adaptaciya-personala/platformy-onbordinga), [CNews](https://www.cnews.ru/news/line/2024-03-13_hr-tech_kompaniya_potok_vnedrila) |
| **Skillaz** | Подбор, видеоинтервью, онбординг, обучение | ИИ в подборе (ранжирование, вакансии) | Не указано | По запросу | [toolfox](https://toolfox.ru/services/s/skillaz) |
| **Хантфлоу (Huntflow)** | ATS | «Хантфлоу AI» рекомендует кандидатов из базы | — | — | [toolfox](https://toolfox.ru/services/s/huntflow/alternatives) |
| **HRChain** | Адаптация, конструктор треков, оценка 360°, eNPS | ИИ-подбор оценивающих | **Telegram-бот** для новичков | от 99 000 ₽/год | [toolfox](https://toolfox.ru/services/hr-adaptaciya-personala/platformy-onbordinga) |
| **Motivity** | Портал, LMS, адаптация, нематериальная мотивация | ИИ-ассистент делает уроки, тесты, переводы | Свой корпоративный мессенджер «Коннект» | Есть бесплатный тариф; от 28 728 ₽/мес | там же |
| **Mirapolis HCM** | Подбор, адаптация, обучение, KPI | Не заявлено | — | По запросу | там же |
| **iSpring** | LMS, мобильное обучение офлайн | Диалоговые тренажёры | — | от 249 ₽/польз./мес | там же |
| **Битрикс24 (+GigaChat)** | Портал, HR-модули | ИИ генерирует тесты по загруженным регламентам | Свой мессенджер | — | [habr](https://habr.com/ru/articles/985294) [не подтв. в деталях] |
| **ЦРТ — ИИ-помощник на GigaChat для промышленности** | Ответы сотрудникам по корпоративным документам | RAG; распознаёт больше 95% отраслевой лексики [вендор] | — | — | [sber.pro](https://sber.pro/publication/gruppa-tsrt-sozdala-ai-pomoschnika-s-gigachat-dlya-promishlennosti/) |
| **Sherpa AI** | LLM-экосистема on-premise, ассистенты, RPA | LLM в закрытом контуре | Teams, Telegram, Bitrix24 | По запросу | [toolfox](https://toolfox.ru/services/hr-adaptaciya-personala/hr-chat-boty-adaptaciya) |
| **Botmother** (конструктор) | Боты общего назначения | ИИ-ассистенты, передача оператору | **МАКС**, Telegram, VK, WhatsApp и др. | от 4 399 ₽/мес | там же |
| **HRlink** | КЭДО: подпись документов, справки, отпуск в приложении | ИИ-чата нет | Своё приложение | — | [App Store](https://apps.apple.com/ru/app/id6741211587) |

Не нашли публичной информации о ИИ- и sentiment-функциях у **Teamforce, Happy Job, WorkBuddy, Talantix, Unicraft, Директум** в рамках этой разведки. Это не значит, что у них таких функций нет: если нужно, проверить отдельно.

**Вывод по РФ:** связки «RAG-ответы + sentiment открытых ответов + эскалация HR + нудж руководителю + дайджест» в одном продукте, да ещё в МАКС, среди найденных решений нет. Ближе всех по отдельным кускам Personik (ответы и перевод на человека) и «Пульс» (ИИ-агент).

### 1.2. Зарубежье

| Решение | Что делает | «Вау»-фичи | Заявленные цифры | Ссылка |
|---|---|---|---|---|
| **Leena AI** | ИИ-ассистент сотрудника (HR/IT), онбординг | Доля самообслуживания **70% прописана в договоре**; персональный онбординг | DCM Shriram: 87% запросов закрыто автоматически; Hima Cement (цемент, онбординг): 85% [вендор] | [DCM Shriram](https://leena.ai/dcm-shriram-HR-digital-transformation), [Hima Cement](https://leena.ai/HimaCement-employee-experience) |
| **Moveworks** | ИИ-поддержка сотрудников (в основном IT) | Работает внутри Slack/Teams, онбординг сотрудников при поглощении (Broadcom) | 54% обращений закрыто автономно, в среднем 7 с; Broadcom: 88% (было ~10% на старте) [вендор] | [datasheet](https://www.moveworks.com/content/dam/moveworksprogram/pdfs/datasheet/Moveworks-Employee-Support.pdf), [Broadcom](https://moveworks.com/case-studies/broadcom) |
| **Enboarder** | Онбординг как «путешествие» с нуджами | **Нуджи руководителю в Slack/Teams/почту**: конкретное действие, а не дашборд. Вовлечённость руководителя как опережающий индикатор удержания (по IDC) | Медтех-компания: −36% текучести за 90 дней, $1,68M экономии; телеком: +20% продуктивности новичков [вендор, клиенты не названы] | [Enboarder blog](https://enboarder.com/blog/manager-role-ai-onboarding/) |
| **Workday Peakon** | Пульс-опросы, «голос сотрудника» | ML-прогноз **риска добровольного ухода** по группам; NLP по комментариям; анализ влияния драйверов | Методика модели не раскрыта | [Workday](https://workday.com/en-us/products/employee-voice/why-peakon-employee-voice-software.html), [admin guide](https://doc.workday.com/admin-guide/en-us/workday-feature-descriptions/workday-peakon-employee-voice/future--peakon-analysis.html) |
| **Microsoft Viva Glint (+Copilot)** | Опросы вовлечённости | Copilot суммирует до 8 000 комментариев, до 10 тем, срез по тональности. **Не отвечает, если комментариев меньше порога конфиденциальности** | — | [Microsoft Learn](https://learn.microsoft.com/en-us/viva/glint/setup/copilot-faqs), [Comments report](https://learn.microsoft.com/en-us/viva/glint/reports/comments-report) |
| **Culture Amp** | Опросы вовлечённости и производительности | ИИ-суммаризация комментариев по вопросам (позитив/негатив/нейтрал + темы + репрезентативные цитаты); с мая 2025 разметка тональности на LLM; минимум 5 содержательных комментариев на вопрос | Пилот у 400+ клиентов: сэкономлено ~6 600 часов HR [вендор] | [support](https://support.cultureamp.com/en/articles/8540095-ai-comment-summaries-in-engagement-results), [ChannelLife](https://channellife.com.au/story/culture-amp-launches-ai-tool-to-streamline-hr-feedback-analysis) |
| **Qualtrics EX** | Опросы, «голос сотрудника» | Описывают «усталость от бездействия»: опрос бессмысленен, если по ответам ничего не меняется | — | [Qualtrics](https://www.qualtrics.com/articles/employee-experience/employee-survey-fatigue/) |
| **Humu** (основан экс-HR-директором Google Ласло Боком) | «Nudge Engine»: ML выбирает маленькие персональные подсказки сотрудникам и руководителям | Поведенческие нуджи по теории Талера | Контролируемых результатов по удержанию публично нет; есть отзыв Sweetgreen | [Wikipedia](https://en.wikipedia.org/wiki/Humu_(software)), [Harvard D3](https://d3.harvard.edu/platform-peopleanalytics/?p=666) |
| **EY Onboarding Buddy** | ИИ-чат для новичков, интеграция с SAP SuccessFactors | — | — | [hh.ru](https://hh.ru/article/31427) |
| **Qlicket** (frontline) | Пульс-опросы для линейного персонала **через планшеты в комнатах отдыха**, геймификация | Канал для сотрудников без ПК | — | [Dealroom](https://app.dealroom.co/companies/teamforce_ai) [не подтв.] |

---

## 2. Кейсы промышленных и крупных российских компаний

| Компания | Что сделано | Результат | Источник |
|---|---|---|---|
| **Норникель** | Адаптация на SAP HCM: личный кабинет **+ чат-бот для рабочих без стационарного компьютера** (партнёр «Эвола») | 1-й год: больше 2000 пользователей, **из них 600 рабочих**; «процесс стал понятнее и прозрачнее» | [ComNews, 08.12.2021](https://www.comnews.ru/digital-economy/content/217830/2021-12-08/2021-w49/nornikel-avtomatiziroval-process-adaptacii-personala-baze-sap) |
| **Сбер** | «Пульс»: ИИ-агент на GigaChat | 86% обращений к боту закрываются без человека; цель — к концу 2026 года автоматически закрывать ≥20% всех обращений в поддержку | [РИАМО, 29.09.2026](https://riamo.ru/news/obschestvo/ii-agent-sbera-vnutri-hr-platformy-puls-86-voprosov-reshajutsja-agentom/) |
| **X5** | Продукт для первого рабочего дня новичков (агентство Nectarin) | Цифр в открытом доступе нет | [Sostav, 12.2024](https://www.sostav.ru/publication/chat-s-kollegamii-x5-group-i-nectarin-71707.html) |
| **Магнит** | Семейство служебных чат-ботов (CraftTalk): HelpDesk, Active Directory | Это сервисные, а не адаптационные боты; цифр нет | [ComNews, 2021](https://www.comnews.ru/digital-economy/content/212877/2021-02-02/2021-w05/crafttalk-pomog-magnitu-razrabotat-semeystvo-chat-botov) |
| **«Маревен Фуд Сэнтрал»** (пищевое производство) | Чат-бот-наставник в мессенджерах: советы и опросы новичков в двух странах | Бот снял с коллег нагрузку консультаций; «новичку не всегда комфортно просить» | [vc.ru](https://vc.ru/future/358296-kak-chat-bot-stal-nastavnikom-dlya-novyh-sotrudnikov-on-daet-poleznye-sovety-v-messendzherah-i-provodit-oprosy) |
| **СИБУР, Северсталь, НЛМК, ММК, ЕВРАЗ, Металлоинвест, Росатом, Газпром нефть, РУСАЛ, МТС** | **Публичных кейсов ИИ-ботов адаптации с цифрами найти не удалось.** | — | — |

**Как подать на защите:** у крупной промышленности публичных ИИ-кейсов адаптации почти нет. Норникель — бот с кнопками на SAP, Сбер — ИИ для поддержки платформы. **Готового «ИИ-наставника для рабочих в национальном мессенджере» в промышленности публично нет**, и это наш аргумент «не копия существующего».

---

## 3. Цифры для слайдов

| # | Цифра | Источник, год | Надёжность |
|---|---|---|---|
| 1 | **40%** сотрудников думали уволиться в первые месяцы; у **58%** из них причина — расхождение ожиданий с реальностью | hh.ru + Edstein, 08.2024 ([New Retail](https://new-retail.ru/novosti/retail/issledovanie_40_sotrudnikov_zadumyvalis_ob_uvolnenii_v_pervye_mesyatsy_raboty/)) | Хорошая (выборка сотрудников не указана, компаний 266) |
| 2 | **59%** сотрудников: на текущем месте онбординга нет; **81%** из них хотели бы его | там же | Хорошая |
| 3 | Желаемые форматы адаптации: наставник **72%**, личные встречи **49%**, приложение в телефоне **14%** | там же | Хорошая |
| 4 | **90%** новичков 2025 года прошли испытательный срок; но у **22%** работодателей до **30%** уходят за первые 3 месяца, а **7%** компаний теряют больше трети новичков | hh.ru, 12.2025 ([Sostav](https://www.sostav.ru/publication/90-kandidatov-uspeshno-proshli-ispytatelnyj-srok-na-novoj-rabote-v-2025-godu-80168.html)) | Хорошая |
| 5 | Причины ухода на испытательном: не понравились задачи **67%**, график **22%**, недопонимание с коллективом **21%**, корпоративная культура **15%**; сложности с руководителем — **33%** (по опыту рекрутеров) | там же | Хорошая |
| 6 | **23%** работников уходили до конца испытательного срока; по инициативе работника это происходит **в 3 раза чаще**, чем по инициативе работодателя | SuperJob ([Клерк](https://klerk.ru/job/news/431028)) | Средняя (год не уточнён) |
| 7 | **64%** россиян хотя бы раз уходили с новой работы в первые полгода; 64% понимали это уже в первый месяц | Dream Job ([finance.mail.ru](https://finance.mail.ru/article/issledovanie-pokazalo-skolko-rossiyan-uhodili-s-novoj-raboty-69217935/)) | Средняя |
| 8 | Только **12%** сотрудников уверенно согласны, что их организация хорошо онбордит; при активном участии руководителя шанс на отличный онбординг **×3,4**; полная продуктивность — около **12 месяцев** | Gallup, 2019 ([Gallup](https://www.gallup.com/workplace/247172/problems-onboarding-program.aspx)) | Высокая (первоисточник) |
| 9 | Сильный онбординг: удержание **+82%**, продуктивность **+70%** | Brandon Hall Group для Glassdoor ([пересказ](https://www.strongdm.com/blog/employee-onboarding-statistics)) | [вторичка] — оригинал не найден, давать с оговоркой |
| 10 | Структурированная программа: новичок на **58%** вероятнее останется через 3 года; **+50%** продуктивности; **69%** «останутся 3 года» | Click Boarding в пересказе SHRM ([SHRM](https://www.shrm.org/in/topics-tools/news/talent-acquisition/dont-underestimate-importance-good-onboarding)) | [вендор] — не исследование SHRM |
| 11 | Замена сотрудника стоит **50–200%** годовой зарплаты (50% для массового персонала, до 200% для руководителей) | Приписывают Gallup/SHRM ([finratings](https://finratings.kz/news/3950-skolko-teriaiut-kompanii-na-uvolneniiakh-i-zamenakh-sotrudnikov/), [joinforma](https://joinforma.com/resources/employee-replacement-costs)) | [вторичка] |
| 12 | РФ: замена одного работника в среднем ≈ **21%** годового дохода | АНКОР, 2010 ([ДП](https://www.dp.ru/a/2010/09/30/Vo_skolko_obhodits_zamen)) | Старая — для консервативной оценки ROI |
| 13 | **47%** HR-руководителей тратят **4+ часа в день** на повторяющиеся вопросы сотрудников | isolved HR Leaders Report 2026 ([isolved](https://www.isolvedhcm.com/blog/ai-agent-employee-self-service)) | [вендор] |
| 14 | **51%** HR тратят минимум половину рабочей недели на рутину; **70%** говорят, что рутина мешает важным задачам | Eagle Hill Consulting ([HR Dive](https://www.hrdive.com/news/hr-pros-say-half-of-their-week-is-spent-on-repetitive-work/831933/)) | Средняя |
| 15 | Только **6%** сотрудников получают мгновенную помощь через ИИ или чат; **36%** ждут ответа HR минимум сутки | ([People Matters](https://sea.peoplemattersglobal.com/news/strategic-hr/employees-frustrated-as-1-in-5-wait-days-for-hr-support-48101)) | Средняя |
| 16 | Только **53%** сотрудников верят, что по результатам опроса что-то сделают | People Insight ([peopleinsight](https://peopleinsight.co.uk/survey-fatigue/)) | [не подтв.] — исходный бенчмарк не назван |
| 17 | ИИ-суммаризация комментариев сэкономила ~**6 600 часов** HR у 400+ компаний в пилоте | Culture Amp ([ChannelLife](https://channellife.com.au/story/culture-amp-launches-ai-tool-to-streamline-hr-feedback-analysis)) | [вендор] |
| 18 | Боты закрывают без человека: Сбер «Пульс» **86%**, Leena **85–87%**, Moveworks **54%** (Broadcom **88%**) | см. таблицу аналогов | [вендор] |
| 19 | Frontline/deskless — **80%** мировой рабочей силы; больше половины deskless имеют ограниченный доступ к почте; **1 из 4** не знает, где найти нужную информацию | Workvivo by Zoom 2025 / Firstup ([Engagedly](https://engagedly.com/hr-glossary/frontline-worker/), [cxm.world](https://cxm.world/?p=127377)) | [вторичка] |
| 20 | РФ: безработица ~**2–2,2%**, дефицит кадров **3–4 млн** человек; острее всего не хватает рабочих специальностей | Росстат в пересказе ([finance.mail.ru](https://finance.mail.ru/article/ryinok-vyizhivaniya-pochemu-bezrabotitsa-v-rossii-mozhet-vyirasti-v-2026-godu-69198150/)) | Средняя |
| 21 | МАКС: DAU **18,2 млн** (окт. 2025), больше **107 млн** регистраций и DAU больше **77 млн** (март 2026) | пресс-служба МАКС / СМИ ([habr](https://habr.com/ru/post/956758), [www1.ru](https://www1.ru/news/2026/03/29/za-odin-god-v-messendzere-max-zaregistrirovalis-107-millionov-polzovatelei-sredi-nix-est-inostrancy.html)) | Средняя (цифры компании) |
| 22 | Москва: **4,5 тыс.** промпредприятий, больше **750 тыс.** занятых (каждый 10-й работающий), к 2030 году — **850 тыс.**; ~4 000 площадок, из них ~700 крупных и средних | mos.ru; Ведомости ([mos.ru](https://www.mos.ru/mayor/themes/13316050/), [Ведомости](https://www.vedomosti.ru/gorod/ourcity/articles/zavodskaya-i-fabrichnaya-moskva-pochemu-stolitse-ponadobilis-svoi-proizvodstva)) | Высокая |
| 23 | ПОЛИПЛАСТИК: **33 завода** (РФ, РБ, РК), **46** торговых домов, крупнейший в РФ и ЕАЭС производитель полимерных трубопроводных систем | hh.ru ([hh.ru](https://hh.ru/employer/3291)) | Высокая; **численность персонала не найдена** |

**Специально по производству** надёжной публичной цифры текучести новичков за 90 дней не нашлось. На слайде лучше показать общерыночные hh.ru №1, 4, 5 и дефицит рабочих №20, а также попросить у заказчика их собственную текучесть на испытательном сроке (это сильнее любого бенчмарка).

**Каркас ROI для слайда (наш расчёт, параметры подставить от заказчика):**
`экономия = (часы HR на типовые вопросы и разбор опросов × доля, которую закроет бот) × ставка HR + (предотвращённые уходы × стоимость замены)`.
Доля автозакрытия: консервативно 50% (Moveworks), оптимистично 85% (Leena, Сбер). Стоимость замены: консервативно 21% годового дохода (АНКОР), типично 50% для массового персонала [вторичка].

---

## 4. UX-проблемы таких ботов и как их обходят

| Проблема | Что известно | Как обходят лучшие |
|---|---|---|
| **Новичок предпочитает людей, а не приложение** | Приложение в телефоне выбирают только 14%, наставника 72% (hh.ru + Edstein) | Enboarder, Humu: бот не заменяет человека, а **подталкивает руководителя и наставника** к действию. Gallup: участие руководителя даёт ×3,4 |
| **Нет ПК и корпоративной почты у рабочих** | Больше половины deskless почти без доступа к почте; 1 из 4 не знает, где искать информацию | Норникель: чат-бот вместо личного кабинета; Qlicket: планшеты в комнате отдыха; Moveworks: работает внутри уже привычного мессенджера |
| **Усталость от опросов и от «бездействия»** | Только ~53% верят, что по опросу что-то сделают [не подтв.]; Qualtrics: «опрос бессмысленен, если ничего не меняется» | Замыкать петлю: показывать сотруднику «вы сказали → мы сделали». Короткие пульсы вместо длинных анкет; опрос привязан к событию (первый день, первая смена) |
| **Недоверие к анонимности, страх, что увидит руководитель** | Важно, чтобы в анонимность **верили**, а не только чтобы она была (Survio) | Glint: Copilot **молчит, если комментариев меньше порога конфиденциальности**; Culture Amp: минимум 5 комментариев. Руководитель видит только агрегаты, HR видит красные флаги. Честная модель: **«конфиденциально», а не «анонимно»** — при красном флаге с тобой свяжется HR, а не руководитель |
| **Галлюцинации и «уверенная ерунда» про регламенты** | Ошибка в зарплате, отпуске или технике безопасности на производстве = потеря доверия или риск для жизни | RAG с цитатой пункта документа, отказ «не знаю — передаю HR» при низкой уверенности; Leena/Moveworks: эскалация в тикет, если ответа нет |
| **Бот надоедает** | — | Нуджи «в нужный момент, одно действие» (Enboarder: конкретная подсказка, а не дашборд), уважение к сменному графику (не писать ночью после смены) |
| **Расхождение ожиданий** (главная причина ухода — 58–67%) | hh.ru 2024/2025 | Pre-boarding от оффера: «что будет в первую смену», фото цеха, СИЗ, маршрут до проходной. Честный опрос «совпало ли с обещанным» на 7-й и 30-й день |

---

## 5. Целевая и потенциальная аудитория

### Сегменты пользователей внутри заказчика
1. **Рабочие производства (сменные, без ПК):** операторы экструзионных линий, наладчики, кладовщики, водители погрузчиков. Канал — личный телефон, короткие сессии в перерыв. Нужно: голос/кнопки, понятный язык, ответы про смены, зарплату, СИЗ, пропуск, столовую. Риск: в цеху может не быть связи [проверить у заказчика].
2. **Офис и ИТР:** инженеры, технологи, продажи (46 торговых домов). Больше текстовых вопросов по регламентам, выше ожидания к UX.
3. **Удалённые площадки:** 33 завода в трёх странах, разные часовые пояса и локальные регламенты, поэтому база знаний должна фильтроваться по площадке.
4. **Иностранные работники (гипотеза):** для промышленности РФ это заметная доля линейного персонала, но **у ПОЛИПЛАСТИКа не подтверждено**. Если есть — нужны ответы на простом русском или на родном языке.
5. **Руководители подразделений и мастера:** получают нуджи и дашборд, времени мало, нужен формат «одна кнопка».
6. **HR / рекрутеры / руководитель подбора:** получают дайджест, красные флаги, обновляют базу знаний PDF без кода.

### Потенциал тиражирования
- **Внутри группы:** 33 завода + 46 торговых домов (hh.ru). Общая численность персонала не найдена — **запросить у заказчика**.
- **Москва (Моспром):** 4,5 тыс. промышленных предприятий, больше 750 тыс. занятых, к 2030 году план 850 тыс. (+100 тыс. рабочих мест), ~700 крупных и средних (mos.ru, Ведомости). То есть до 100 тыс. новых людей проходят адаптацию только за счёт роста.
- **Канал МАКС:** большая аудитория (77 млн DAU, март 2026), и госсектору рекомендуют переходить на МАКС (Ведомости, 2025; **официального обязательного требования для промышленности не найдено**). Для бизнес-ботов нужна верификация юрлица (ИНН, Госуслуги, банк), API на platform-api.max.ru ([habr](https://habr.com/ru/articles/923364), [green-api](https://green-api.com/max-bot-api/docs/before-start)) [не подтв. по официальной документации].
- **Фон рынка труда:** безработица ~2%, дефицит 3–4 млн человек — удержать дешевле, чем найти.

---

## 6. Идеи, которые стоит украсть (14)

| # | Фича | У кого подсмотрено | Почему подходит заводу |
|---|---|---|---|
| 1 | **Нудж мастеру «проведи встречу с Ивановым сегодня, вот 3 вопроса»**: одно конкретное действие в мессенджере, а не ссылка на дашборд | Enboarder (manager nudges), Humu | Gallup ×3,4 при участии руководителя; у мастера нет времени смотреть дашборды |
| 2 | **Вовлечённость руководителя как опережающая метрика** на дашборде (сколько нуджей выполнено) | Enboarder ссылается на IDC | Удержание видно через 90 дней, а выполнение встреч — сразу |
| 3 | **Порог конфиденциальности**: в дайджесте руководителю нет цитат и сводки, если в группе меньше N ответов | Viva Glint Copilot, Culture Amp (≥5 комментариев) | Бригада из 4 человек = деанонимизация; это снимает страх «увидит мастер» |
| 4 | **ИИ-сводка комментариев «тема + тональность + 1–2 репрезентативные цитаты»** вместо чтения всех ответов | Culture Amp, Glint | Прямо бьёт в «40% времени HR на разбор текстов» |
| 5 | **Сравнение настроений между площадками и сменами** (где хуже и почему) | Culture Amp (сравнение групп, 2026) | 33 завода: HR видит проблемную площадку, а не среднюю температуру |
| 6 | **Прогноз риска ухода** по сигналам (тональность, пропущенные пульсы, «не совпало с ожиданиями») | Workday Peakon (attrition risk) | Даёт HR приоритетный список «кому позвонить на этой неделе» |
| 7 | **Гарантированная доля самообслуживания как KPI пилота** (например, «≥60% вопросов без HR, иначе…») | Leena AI (70% в договоре) | Окупаемость на языке жюри и заказчика: измеримое обязательство |
| 8 | **Ответ с цитатой пункта регламента + «не уверен — передаю человеку»** | Leena, Moveworks (эскалация в тикет) | Контроль галлюцинаций; для охраны труда цена ошибки высока |
| 9 | **Канал, который уже в кармане** — без нового приложения и без корпоративной почты | Норникель (бот для рабочих без ПК), Moveworks (внутри Slack/Teams) | У сменного рабочего есть только телефон; МАКС предустановлен и национальный |
| 10 | **Pre-boarding от оффера**: «что будет в первую смену», фото проходной, СИЗ, маршрут, контакт мастера | Enboarder («experience-driven onboarding»), EY Onboarding Buddy | Главная причина ухода — ожидания ≠ реальность (58–67%, hh.ru) |
| 11 | **Пульс «совпало ли с обещанным?»** на 7-й и 30-й день с эскалацией при «нет» | Логика hh.ru-причин + Peakon-драйверы | Ловим главную причину ухода до того, как она сработает |
| 12 | **«Вы сказали — мы сделали»**: бот сообщает новичкам, что изменили по их ответам | Qualtrics/Perceptyx (борьба с усталостью от бездействия) | Поднимает долю ответов на следующих пульсах и доверие |
| 13 | **Сбор пульса через общий планшет/QR в комнате отдыха** для тех, у кого нет МАКС или смартфона | Qlicket (frontline-пульсы на планшетах) | Закрывает «хвост» рабочих без смартфона, честно для аналитики |
| 14 | **ИИ генерирует мини-тест по загруженному регламенту** (проверка, что новичок понял ТБ/СИЗ) | Битрикс24 + GigaChat, Motivity AI-ассистент | HR загрузил PDF — получил и ответы, и проверку усвоения без кода |

---

## 7. Чего не нашли и что запросить у заказчика

- Численность персонала ПОЛИПЛАСТИКа, доля рабочих, текучесть за испытательный срок и за 90 дней **по их данным** — для ROI это сильнее любого бенчмарка.
- Есть ли иностранные работники и есть ли связь в цехах.
- Публичных ИИ-кейсов адаптации у СИБУРа, Северстали, НЛМК, ММК, ЕВРАЗа, Росатома, Газпром нефти не найдено. Ссылаться на них нельзя.
- Оригиналы Brandon Hall (82%/70%) и «50–200% годовой зарплаты» не найдены: на слайде давать с пометкой «по данным вторичных обзоров» или заменить на Gallup и hh.ru.
- Функции Teamforce, Happy Job, WorkBuddy, Talantix, Unicraft, Директум в этой разведке не проверены.

---

## Источники

**Россия — рынок и кейсы**
- Сбер «Пульс», ИИ-агент 86%: https://riamo.ru/news/obschestvo/ii-agent-sbera-vnutri-hr-platformy-puls-86-voprosov-reshajutsja-agentom/
- Норникель, адаптация + бот для рабочих: https://www.comnews.ru/digital-economy/content/217830/2021-12-08/2021-w49/nornikel-avtomatiziroval-process-adaptacii-personala-baze-sap
- Платформы онбординга (каталог): https://toolfox.ru/services/hr-adaptaciya-personala/platformy-onbordinga
- HR-чат-боты адаптации (каталог): https://toolfox.ru/services/hr-adaptaciya-personala/hr-chat-boty-adaptaciya
- Personik: https://toolfox.ru/services/s/personik-ai , https://soware.ru/products/personik , https://ruward.ru/catalog/personik-ai/
- Поток + YandexGPT: https://www.cnews.ru/news/line/2024-03-13_hr-tech_kompaniya_potok_vnedrila
- Skillaz: https://toolfox.ru/services/s/skillaz
- ЦРТ, ИИ-помощник на GigaChat для промышленности: https://sber.pro/publication/gruppa-tsrt-sozdala-ai-pomoschnika-s-gigachat-dlya-promishlennosti/
- Битрикс24 + GigaChat: https://habr.com/ru/articles/985294
- HRlink: https://apps.apple.com/ru/app/id6741211587
- X5 + Nectarin: https://www.sostav.ru/publication/chat-s-kollegamii-x5-group-i-nectarin-71707.html
- Магнит + CraftTalk: https://www.comnews.ru/digital-economy/content/212877/2021-02-02/2021-w05/crafttalk-pomog-magnitu-razrabotat-semeystvo-chat-botov
- Маревен Фуд, чат-бот-наставник: https://vc.ru/future/358296-kak-chat-bot-stal-nastavnikom-dlya-novyh-sotrudnikov-on-daet-poleznye-sovety-v-messendzherah-i-provodit-oprosy
- hh.ru, боты в HR (EY Onboarding Buddy): https://hh.ru/article/31427

**Россия — цифры**
- hh.ru + Edstein, 40% думали уволиться: https://new-retail.ru/novosti/retail/issledovanie_40_sotrudnikov_zadumyvalis_ob_uvolnenii_v_pervye_mesyatsy_raboty/
- hh.ru, испытательный срок 2025: https://www.sostav.ru/publication/90-kandidatov-uspeshno-proshli-ispytatelnyj-srok-na-novoj-rabote-v-2025-godu-80168.html
- SuperJob, уход на испытательном: https://klerk.ru/job/news/431028
- Dream Job, уход в первые полгода: https://finance.mail.ru/article/issledovanie-pokazalo-skolko-rossiyan-uhodili-s-novoj-raboty-69217935/
- АНКОР, стоимость замены (2010): https://www.dp.ru/a/2010/09/30/Vo_skolko_obhodits_zamen
- Дефицит кадров 2026: https://finance.mail.ru/article/ryinok-vyizhivaniya-pochemu-bezrabotitsa-v-rossii-mozhet-vyirasti-v-2026-godu-69198150/
- Москва, промышленность: https://www.mos.ru/mayor/themes/13316050/ , https://www.vedomosti.ru/gorod/ourcity/articles/zavodskaya-i-fabrichnaya-moskva-pochemu-stolitse-ponadobilis-svoi-proizvodstva , https://360.ru/tekst/moskva/liksutov-promyshlennye-predprijatija-moskvy-dadut-rabotu-esche-100-tysjacham-chelovek/
- ПОЛИПЛАСТИК на hh.ru: https://hh.ru/employer/3291
- МАКС, аудитория: https://habr.com/ru/post/956758 , https://www1.ru/news/2026/03/29/za-odin-god-v-messendzere-max-zaregistrirovalis-107-millionov-polzovatelei-sredi-nix-est-inostrancy.html
- МАКС, госслужащим рекомендовано: https://novayagazeta.eu/articles/2025/07/31/vedomosti-regionalnym-chinovnikam-rekomendovali-aktivnee-polzovatsia-messendzherom-max-news
- МАКС, платформа для ботов: https://green-api.com/max-bot-api/docs/before-start , https://habr.com/ru/articles/923364 , https://ppc.world/articles/kak-sozdat-chat-bota-v-max-v-2026-godu-poshagovaya-instrukciya-i-top-10-konstruktorov/

**Зарубежье**
- Gallup, онбординг 12% и ×3,4: https://www.gallup.com/workplace/247172/problems-onboarding-program.aspx
- SHRM (Click Boarding 69%/58%): https://www.shrm.org/in/topics-tools/news/talent-acquisition/dont-underestimate-importance-good-onboarding
- Brandon Hall 82%/70% (вторичка): https://www.strongdm.com/blog/employee-onboarding-statistics
- Стоимость замены 50–200% (вторичка): https://finratings.kz/news/3950-skolko-teriaiut-kompanii-na-uvolneniiakh-i-zamenakh-sotrudnikov/ , https://joinforma.com/resources/employee-replacement-costs
- Leena AI: https://leena.ai/dcm-shriram-HR-digital-transformation , https://leena.ai/HimaCement-employee-experience
- Moveworks: https://www.moveworks.com/content/dam/moveworksprogram/pdfs/datasheet/Moveworks-Employee-Support.pdf , https://moveworks.com/case-studies/broadcom
- Enboarder: https://enboarder.com/blog/manager-role-ai-onboarding/
- Workday Peakon: https://workday.com/en-us/products/employee-voice/why-peakon-employee-voice-software.html , https://doc.workday.com/admin-guide/en-us/workday-feature-descriptions/workday-peakon-employee-voice/future--peakon-analysis.html
- Viva Glint Copilot: https://learn.microsoft.com/en-us/viva/glint/setup/copilot-faqs , https://learn.microsoft.com/en-us/viva/glint/reports/comments-report
- Culture Amp: https://support.cultureamp.com/en/articles/8540095-ai-comment-summaries-in-engagement-results , https://channellife.com.au/story/culture-amp-launches-ai-tool-to-streamline-hr-feedback-analysis
- Humu: https://en.wikipedia.org/wiki/Humu_(software) , https://d3.harvard.edu/platform-peopleanalytics/?p=666
- Qualtrics, усталость от опросов: https://www.qualtrics.com/articles/employee-experience/employee-survey-fatigue/
- People Insight, 53%: https://peopleinsight.co.uk/survey-fatigue/
- isolved 2026 (47%): https://www.isolvedhcm.com/blog/ai-agent-employee-self-service
- Eagle Hill (51%): https://www.hrdive.com/news/hr-pros-say-half-of-their-week-is-spent-on-repetitive-work/831933/
- Ожидание ответа HR (6%/36%): https://sea.peoplemattersglobal.com/news/strategic-hr/employees-frustrated-as-1-in-5-wait-days-for-hr-support-48101
- Deskless / frontline: https://engagedly.com/hr-glossary/frontline-worker/ , https://cxm.world/?p=127377
- Qlicket (frontline-пульсы на планшетах): https://app.dealroom.co/companies/teamforce_ai
- Анонимность опросов (Survio): https://intercom.help/survio-fe758013f522/ru/articles/15704269-как-провести-опрос-удовлетворённости-сотрудников-на-который-действительно-отвечают
