# Ручная выборка

`labels.jsonl` — 190 новостей из живого прогона v0.3.0 (разные источники и темы).
Поля `category` и `sentiment` проставлены вручную: рубрика из списка в `config/categories.yaml`,
тональность — новостной тон заголовка (positive / neutral / negative), а не «настроение модели».

Повторить расчёт:

```bash
fast-parser eval --set eval/labels.jsonl
```

Абстенция (модель не выбрала рубрику) считается ошибкой и в accuracy, и в recall.
