# Повторяющиеся события

Исходный VCALENDAR копируется целиком; меняются нужные свойства. VEVENT master, exceptions, VALARM, VTIMEZONE, неизвестные X-properties/parameters сохраняются. Floating time можно записать и прочитать, но expansion и поиск слотов без явной timezone запрещены. All-day сохраняет DATE и исключительную конечную дату.

## Single и series

`move_single` создаёт/меняет exception с исходным RECURRENCE-ID. `cancel_single` отмечает такой exception CANCELLED. `shift_series` сдвигает master, RDATE/EXDATE и recurrence identities. Ранее перемещённые/отменённые exceptions сохраняют фактическое абсолютное время (`preserve_absolute`).

Несуществующее и неоднозначное локальное время DST отклоняется. При смене зоны значение получает свой TZID/UTC encoding, а непрозрачные параметры сохраняются. Native RANGE exceptions нельзя молча переписать обычным series shift. Перенос BYDAY series на другой день недели отклоняется, пока преобразование правила не реализовано.

## Future split

Граница включительна по исходному identity, а не фактическому времени перемещённой встречи. Поддержан один простой WEEKLY rule с INTERVAL, COUNT либо UNTIL, WKST и, при наличии, единственным BYDAY, совпадающим с DTSTART. Другие правила явно unsupported.

1. Сохранить приватный split plan с before/past/future payload, версиями, новой UID/href и remapping exceptions.
2. Создать новую будущую серию условным PUT на стабильный href.
3. Условно обрезать прошлую; обе операции проходят общий journal/read-back.
4. При подтверждённом отказе trim удалить новую часть только при совпадении её версии и содержимого с нашим receipt.
5. При неизвестном trim сохранить обе части и сверить результат. Компенсация здесь могла бы удалить уже успешно перенесённое будущее.

COUNT делится по обычным recurrence positions, отмены не увеличивают COUNT. UNTIL корректируется на сдвиг. Бессрочная серия сохраняется как RRULE. EXDATE/RDATE и exceptions распределяются по границе; будущие exception identities remap, фактическое время индивидуальных переносов сохраняется.

Это несколько внешних шагов без общей атомарности: временно возможны две перекрывающиеся серии. Receipt раскрывает partial результат. Calendar sharing и invitations не входят в преобразование.

## Native probe и сравнение

Harness отдельно отправляет `RECURRENCE-ID;RANGE=THISANDFUTURE`. Конструкция определена [RFC 5545](https://datatracker.ietf.org/doc/html/rfc5545#section-3.8.4.4); её наличие в ответе не доказывает поведение iCloud/Apple клиента. До визуальной приёмки native capability unverified.

Сравнение семантики допускает перестановку верхних компонентов/свойств и изменения DTSTAMP, LAST-MODIFIED, CREATED, SEQUENCE. Остальные свойства, параметры, timezone definition и alarms сравниваются консервативно. Эквивалентная нормализация провайдером может дать `needs_attention`; это повод исследовать конкретный diff, а не ослаблять сравнение молча.

Fixture timezone definitions ограничены 2020–2035. Поддержка произвольных исторических/нестандартных VTIMEZONE и invitation series требует отдельной приёмки. Матрица доказывает конкретные примеры, а не все варианты iCalendar.
