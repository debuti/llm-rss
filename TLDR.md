**LLM-RSS**: filtro diario de noticias con Claude. Lee ~170 entradas al día y publica un RSS con solo las 3–10 que apuntan a ideas realmente nuevas, cada una con un resumen sencillo, su "por qué importa" y un glosario de siglas, más un feed semanal de tendencias.

- **Fuentes**: RSS (Hackaday, Lobsters, arXiv…), Hacker News y GitHub trending.
- **Dedup**: SQLite; la misma noticia en varias fuentes cuenta como señal.
- **Prefiltro**: Haiku descarta la paja mirando solo los títulos.
- **Puntuación**: Opus valora novedad, impacto, evidencia y hype (la nota final la calcula el código) y escribe la explicación sencilla y el glosario.
- **Feeds**: feed.xml, weekly.xml e index.html en GitHub Pages.
- **Semanal**: tags en auge más una nota de Opus sobre lo que está emergiendo.
- **Actions**: ejecución diaria; estado en la cache, con copia en artifact y recuperación si se pierde.
