// The official Typst release includes both defaults. Select installed
// proportional typefaces with --input serif="Charter" --input sans="PT Sans".
#let serif = sys.inputs.at("serif", default: "Libertinus Serif")
#let sans = sys.inputs.at("sans", default: "DejaVu Sans Mono")

#let bg-paper = rgb("#FAF8F5")
#let ink = rgb("#1C1B18")
#let ink-muted = rgb("#6E6A63")
#let ink-subtle = rgb("#9C968D")
#let accent = rgb("#8C3F2B")
#let card-bg = rgb("#F2EFE9")
#let note-bg = rgb("#F4F1EA")
#let border-subtle = rgb("#E2DDD5")

#let scene-break() = {
  v(0.8em)
  align(center)[
    #text(fill: accent, size: 6.5pt, tracking: 0.6em)[◆ ◆ ◆]
  ]
  v(0.8em)
}

#let riddle-box(body) = {
  v(0.4em)
  block(
    width: 100%,
    fill: card-bg,
    inset: (left: 13pt, right: 13pt, top: 10pt, bottom: 10pt),
    radius: (right: 3pt),
    stroke: (left: 1.6pt + accent),
    breakable: false,
  )[
    #set text(size: 9.7pt, style: "italic", fill: rgb("#2C2A26"))
    #set par(leading: 0.62em, spacing: 0.55em, justify: true)
    #body
  ]
  v(0.75em)
}

#let epigraph-box(body) = riddle-box(body)

#let pull-quote(body) = {
  v(0.45em)
  block(
    width: 100%,
    inset: (left: 12pt, right: 8pt, top: 4pt, bottom: 4pt),
    stroke: (left: 1pt + ink-subtle),
  )[
    #text(size: 10.2pt, style: "italic", fill: ink)[#body]
  ]
  v(0.45em)
}

#let author-note(label: "В ПЛАНЕ ДОРАБОТКИ", body) = {
  v(0.25em)
  block(
    width: 100%,
    fill: note-bg,
    stroke: 0.5pt + border-subtle,
    radius: 3pt,
    inset: (x: 10pt, y: 6pt),
  )[
    #set par(justify: false, leading: 0.48em)
    #text(font: sans, size: 6.8pt, weight: "bold", tracking: 0.1em, fill: accent)[#upper(label) · ]
    #text(font: sans, size: 8.2pt, fill: ink-muted)[#body]
  ]
  v(0.25em)
}

#let chapter-heading(num, title) = {
  pagebreak(weak: true)
  v(14mm)
  if num != none {
    text(
      font: sans,
      size: 7.8pt,
      weight: "medium",
      tracking: 0.18em,
      fill: accent,
      upper[Глава #num],
    )
    v(2mm)
  }
  heading(level: 1, outlined: true)[#title]
  v(3mm)
  line(length: 22mm, stroke: 1.1pt + accent)
  v(5mm)
}

#let minimalist-book(
  title: "Название книги",
  subtitle: none,
  authors: (),
  rubric: "ДОКУМЕНТАЛЬНАЯ ПРОЗА",
  edition-note: "АВТОРСКОЕ ИЗДАНИЕ",
  lang: "ru",
  show-toc: true,
  toc-title: "Оглавление",
  body,
) = {
  set document(title: title, author: authors)

  set page(
    width: 152mm,
    height: 228mm,
    fill: bg-paper,
    margin: (top: 24mm, bottom: 25mm, inside: 22mm, outside: 22mm),
    header: context {
      let pg = counter(page).get().first()
      if pg > 2 {
        let h1s = query(heading.where(level: 1).after(here()))
        let is-ch-start = h1s.len() > 0 and h1s.first().location().page() == pg
        if not is-ch-start {
          let prev = query(heading.where(level: 1).before(here()))
          if prev.len() > 0 {
            let ch-title = prev.last().body
            set text(hyphenate: false)
            set par(justify: false, leading: 0.35em)
            grid(
              columns: (auto, 1fr),
              gutter: 14pt,
              align: (left + bottom, right + bottom),
              text(font: sans, size: 6.5pt, tracking: 0.08em, fill: ink-subtle, upper[#title]),
              text(font: serif, size: 7.5pt, style: "italic", fill: ink-muted, ch-title),
            )
            v(-2pt)
            line(length: 100%, stroke: 0.35pt + border-subtle)
          }
        }
      }
    },
    footer: context {
      let pg = counter(page).get().first()
      if pg > 1 {
        align(center)[
          #text(font: sans, size: 8pt, tracking: 0.08em, fill: ink-muted)[#pg]
        ]
      }
    },
  )

  set text(
    font: serif,
    size: 10.4pt,
    fill: ink,
    lang: lang,
    region: lang,
    hyphenate: true,
  )

  set par(
    justify: true,
    leading: 0.68em,
    spacing: 0.82em,
    first-line-indent: 0pt,
  )

  set list(
    marker: text(fill: accent, size: 7pt)[•],
    indent: 4pt,
    body-indent: 7pt,
    spacing: 0.55em,
  )

  show heading.where(level: 1): it => {
    set text(hyphenate: false)
    set par(justify: false, leading: 0.32em)
    text(font: serif, size: 18.5pt, weight: "bold", fill: ink)[#it.body]
  }

  show heading.where(level: 2): it => {
    v(1.1em, weak: true)
    block(sticky: true)[
      #set text(hyphenate: false)
      #set par(justify: false)
      #text(font: serif, size: 12.2pt, weight: "bold", fill: ink)[#it.body]
    ]
    v(0.45em, weak: true)
  }

  show heading.where(level: 3): it => {
    v(0.85em, weak: true)
    block(sticky: true)[
      #set text(hyphenate: false)
      #set par(justify: false)
      #text(font: sans, size: 9.2pt, weight: "bold", tracking: 0.05em, fill: accent, upper[#it.body])
    ]
    v(0.35em, weak: true)
  }

  // Title Page
  page(header: none, footer: none)[
    #set text(hyphenate: false)
    #v(18mm)
    #if rubric != none [
      #text(font: sans, size: 8pt, tracking: 0.22em, fill: ink-muted)[#upper(rubric)]
      #v(5mm)
      #line(length: 100%, stroke: 0.5pt + border-subtle)
    ]
    #v(26mm)
    #par(justify: false, leading: 0.28em)[
      #text(font: serif, size: 25pt, weight: "bold", fill: ink)[#upper(title)]
    ]
    #if subtitle != none [
      #v(6mm)
      #par(justify: false, leading: 0.45em)[
        #text(font: sans, size: 10pt, tracking: 0.14em, fill: accent)[#upper(subtitle)]
      ]
    ]
    #v(8mm)
    #line(length: 28mm, stroke: 1.2pt + accent)
    #v(1fr)
    #grid(
      columns: (1fr, auto),
      align: (left + bottom, right + bottom),
      [
        #for a in authors [
          #text(font: serif, size: 12pt, weight: "bold", fill: ink)[#a] \
          #v(2pt)
        ]
      ],
      [
        #if edition-note != none [
          #text(font: sans, size: 7.5pt, tracking: 0.12em, fill: ink-subtle)[#upper(edition-note)]
        ]
      ]
    )
    #v(6mm)
  ]

  // Table of Contents
  if show-toc [
    #page(header: none)[
      #v(10mm)
      #text(font: sans, size: 7.8pt, weight: "medium", tracking: 0.18em, fill: accent)[НАВИГАЦИЯ]
      #v(2mm)
      #par(justify: false)[#text(font: serif, size: 18pt, weight: "bold")[#toc-title]]
      #v(2mm)
      #line(length: 22mm, stroke: 1.1pt + accent)
      #v(6mm)
      #show outline.entry.where(level: 1): it => {
        v(4.5pt, weak: true)
        link(it.element.location())[
          #grid(
            columns: (1fr, auto),
            gutter: 12pt,
            align: (left + bottom, right + bottom),
            par(justify: false, leading: 0.4em)[#text(font: serif, size: 9.8pt, fill: ink)[#it.element.body]],
            text(font: sans, size: 8.5pt, fill: ink-muted)[#it.page()],
          )
        ]
      }
      #outline(title: none, depth: 1, indent: 0pt)
    ]
  ]

  body
}
