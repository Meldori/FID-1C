---
tags: [skill-creator]
max_turns: 40
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

Импорт из 1С в Future IT Dent уже прогнали. В папке work лежит разобранная выгрузка 1С (V8Exch_Data), в папке db — то, что выгрузили из тенанта после загрузки. Проверь, всё ли загрузилось и всё ли корректно, и дай итог в report.md — по каждой сущности сколько из скольки.

Input files: work/V8Exch_Data/*.json, db/*.json
