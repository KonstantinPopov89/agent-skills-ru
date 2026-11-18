// The official Typst release includes both defaults. Select installed
// proportional typefaces with --input serif="Charter" --input sans="PT Sans".
#let serif = sys.inputs.at("serif", default: "Libertinus Serif")
#let sans = sys.inputs.at("sans", default: "DejaVu Sans Mono")

#let paper = rgb("#FAF8F5")
#let ink = rgb("#1C1B18")
#let muted = rgb("#6E6A63")
#let subtle = rgb("#9C968D")
#let accent = rgb("#8C3F2B")
#let card = rgb("#F2EFE9")
#let line-c = rgb("#E2DDD5")

// Рубрика раздела: капитель-гротеск в акценте + тонкая линия.
#let rubric(title) = {
  text(font: sans, size: 7pt, fill: accent, tracking: 0.08em, weight: "bold")[#upper(title)]
  v(-1mm)
  line(length: 100%, stroke: 0.5pt + line-c)
}

// Раздел. newpage: false — продолжить на той же полосе (для коротких хвостов).
#let section(title, lead: none, newpage: true) = {
  if newpage { pagebreak(weak: true) } else { v(8mm) }
  v(2mm)
  rubric(title)
  if lead != none { text(fill: muted, size: 9pt)[#lead]; v(1mm) }
}

// Малый раздел внутри полосы (после основного текста, без новой страницы).
#let minor(title) = { v(6mm); rubric(title) }

// Строка «метка — текст»: метка мелким серым гротеском в колонке 21 мм.
#let row(label, body) = grid(
  columns: (21mm, 1fr), column-gutter: 3mm,
  pad(top: 1.3pt, text(font: sans, size: 7.2pt, fill: subtle, weight: "medium")[#label]),
  body,
)

// Подзаголовок внутри раздела (гротеск, жирный).
#let label-head(body) = { text(font: sans, size: 8.5pt, weight: "bold")[#body]; v(1mm) }

// Карточка позиции: заголовок + код/имя в акценте + строки метка—текст. Не рвётся между полосами.
// rows: массив пар ([Метка], [Текст]); note — серая оговорка «Важно».
#let entry(title, code: none, rows: (), note: none, note-label: [Важно]) = block(breakable: false, above: 5.5mm, below: 0mm)[
  #text(font: sans, size: 10pt, weight: "bold")[#title]
  #if code != none [#h(2mm) #text(font: sans, size: 7.8pt, fill: accent)[#code]]
  #v(-0.6mm)
  #stack(spacing: 1.6mm,
    ..rows.map(r => row(r.at(0), r.at(1))),
    ..if note != none { (row(note-label, text(fill: muted)[#note]),) },
  )
]

// Выделенный блок: тёплая подложка, левая акцентная линейка.
#let callout(body) = block(fill: card, inset: (x: 4mm, y: 3.5mm), radius: 1.5pt, width: 100%,
  stroke: (left: 1.6pt + accent))[
  #set par(spacing: 2.2mm)
  #body
]

// Сноска-оговорка мелким серым.
#let aside(body) = text(size: 8.6pt, fill: muted)[#body]

#let guide(title: [], subtitle: none, kicker: none, author: none, running: none, doc) = {
  set document(title: title)
  set page(
    width: 152mm, height: 228mm, fill: paper,
    margin: (top: 22mm, bottom: 22mm, inside: 20mm, outside: 20mm),
    header: context {
      if counter(page).get().first() > 1 [
        #set text(font: sans, size: 7pt, fill: subtle, tracking: 0.04em)
        #upper(if running != none { running } else { title }) #h(1fr) #counter(page).display()
      ]
    },
  )
  set text(font: serif, size: 9.6pt, fill: ink, lang: "ru", hyphenate: true)
  set par(leading: 0.62em, spacing: 0.75em, justify: false)
  set enum(spacing: 1.8mm)
  set list(spacing: 1.8mm, marker: text(fill: accent)[–])
  show raw: set text(size: 0.92em)

  // Титул
  page(header: none)[
    #v(18mm)
    #if kicker != none { text(font: sans, size: 7.5pt, fill: subtle, tracking: 0.1em)[#upper(kicker)] }
    #v(6mm)
    #text(font: sans, size: 24pt, weight: "bold", tracking: -0.01em)[#title]
    #if subtitle != none { v(4mm); text(size: 10.5pt, fill: accent)[#subtitle] }
    #v(5mm)
    #line(length: 28mm, stroke: 1pt + accent)
    #v(1fr)
    #if author != none { text(font: sans, size: 8pt, fill: muted)[#author] }
  ]
  doc
}
