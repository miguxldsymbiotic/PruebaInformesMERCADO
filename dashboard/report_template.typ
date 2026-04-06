#let template(
  title: "Informe de Mercado de Educación Superior",
  subtitle: "Análisis Estratégico y Tendencias SNIES/OLE",
  company: "UNIMINUTO - Symbiotic",
  logo: "logo_symbiotic.svg",
  authors: ("SymbioTIC By UNIMINUTO",),
  date: "2026-03-30",
  body
) = {
  // Configuración de página
  set page(
    paper: "a4",
    margin: (x: 2.5cm, y: 3cm),
    header: context {
      if counter(page).get().first() > 1 {
        set text(8pt, gray)
        grid(
          columns: (1fr, 1fr),
          align(left, company),
          align(right, title)
        )
        line(length: 100%, stroke: 0.5pt + gray)
      }
    },
    footer: context {
      set text(8pt, gray)
      line(length: 100%, stroke: 0.5pt + gray)
      grid(
        columns: (1fr, 1fr),
        align(left, date),
        align(right, counter(page).display())
      )
    },
  )

  // Fuentes y estilo
  set text(font: "Trebuchet MS", size: 10pt, lang: "es")
  
  // Estilo de tablas concisas
  set table(
    inset: 4pt,
    stroke: 0.5pt + rgb("#dddddd"),
    fill: (x, y) => if y == 0 { rgb("#f8f9fa") } else { none },
  )
  show table.cell.where(y: 0): set text(weight: "bold", fill: rgb("#1A05A2"), size: 9pt)

  // Estilo de encabezados
  show heading: set text(fill: rgb("#1A05A2"))
  show heading.where(level: 1): it => {
    pagebreak(weak: true)
    v(1.5em)
    it
    v(1em)
  }

  // --- PORTADA ---
  page[
    #set align(center)
    #v(3cm)
    #if logo != none {
      image(logo, width: 45%)
    }
    
    #v(4fr)
    
    #text(28pt, weight: "bold", fill: rgb("#1A05A2"), title) \
    #v(0.4em)
    #text(18pt, weight: "light", style: "italic", subtitle)
    
    #v(1fr)
    
    #line(length: 100%, stroke: 2pt + rgb("#1A05A2"))
    
    #v(1fr)
    
    #text(12pt, authors.join(", ")) \
    #text(12pt, date)
    
    #v(2fr)
  ]

  // --- TABLA DE CONTENIDO ---
  outline(indent: auto, depth: 3)
  pagebreak()

  // --- CUERPO ---
  body
}

// Funciones de utilidad para el informe educativo
#let kpi_grid(..items) = {
  grid(
    columns: (1fr, 1fr, 1fr),
    gutter: 12pt,
    ..items
  )
}

#let kpi_box(label, value) = {
  rect(
    width: 100%,
    fill: rgb("#f8f9fa"),
    stroke: (left: 3pt + rgb("#1A05A2")),
    inset: 8pt,
    [
      #set align(left)
      #text(6.5pt, weight: "bold", fill: gray, upper(label)) \
      #v(0.1em)
      #text(14pt, weight: "bold", fill: rgb("#1A05A2"), value)
    ]
  )
}

#let technical_note(body) = {
  rect(
    width: 100%,
    fill: rgb("#eef2ff"),
    radius: 4pt,
    inset: 12pt,
    stroke: none,
    [
      #text(8.5pt, weight: "semibold", fill: rgb("#1A05A2"), "NOTAS METODOLÓGICAS") \
      #v(0.1em)
      #text(8.5pt, style: "italic", fill: rgb("#444466"), body)
    ]
  )
}

#let plot_grid(..images) = {
  grid(
    columns: (1fr, 1fr),
    gutter: 15pt,
    ..images.pos().map(img => {
      block(
        width: 100%,
        spacing: 0pt,
        [
          #image(img, width: 100%)
          #v(2pt)
        ]
      )
    })
  )
}
