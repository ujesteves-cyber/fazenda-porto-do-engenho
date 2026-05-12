"""Geração de relatórios PDF para o PecIA usando ReportLab."""
import os
from datetime import datetime
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph

BRAND_BORDO = colors.HexColor('#D11419')
BRAND_GRAFITE = colors.HexColor('#2E2E2E')
GRAY_MUTED = colors.HexColor('#666666')
GRAY_LINE = colors.HexColor('#CCCCCC')
GRAY_ZEBRA = colors.HexColor('#FAFAFA')


def _fmt(v, n=2):
    if v is None or v == '':
        return '-'
    try:
        return f'{float(v):.{n}f}'
    except (TypeError, ValueError):
        return str(v)


def _draw_chrome(logo_path):
    """Retorna função (canvas, doc) que desenha cabeçalho + rodapé."""
    def _chrome(canvas, doc):
        canvas.saveState()
        width, height = doc.pagesize
        y_top = height - 1.2 * cm

        if logo_path and os.path.exists(logo_path):
            try:
                canvas.drawImage(
                    logo_path, 1.5 * cm, y_top - 1.0 * cm,
                    width=1.4 * cm, height=1.4 * cm,
                    preserveAspectRatio=True, mask='auto'
                )
            except Exception:
                pass

        canvas.setFillColor(BRAND_GRAFITE)
        canvas.setFont('Helvetica-Bold', 11)
        canvas.drawString(3.3 * cm, y_top - 0.2 * cm, 'Fazenda Porto do Engenho')
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(GRAY_MUTED)
        canvas.drawString(3.3 * cm, y_top - 0.6 * cm, 'Nelore Provado · Cariacica-ES')

        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(BRAND_GRAFITE)
        canvas.drawRightString(
            width - 1.5 * cm, y_top - 0.2 * cm,
            'Gerado em ' + datetime.now().strftime('%d/%m/%Y %H:%M')
        )

        canvas.setStrokeColor(BRAND_BORDO)
        canvas.setLineWidth(1.5)
        canvas.line(1.5 * cm, y_top - 1.3 * cm, width - 1.5 * cm, y_top - 1.3 * cm)

        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#999999'))
        canvas.drawCentredString(width / 2, 1 * cm, f'Página {canvas.getPageNumber()}')

        canvas.restoreState()
    return _chrome


def _table_style():
    return TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BRAND_BORDO),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 7.5),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.25, GRAY_LINE),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, GRAY_ZEBRA]),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ])


def _styles():
    base = getSampleStyleSheet()
    title = ParagraphStyle(
        'PecIATitle', parent=base['Heading1'],
        fontSize=14, textColor=BRAND_GRAFITE, spaceAfter=4
    )
    sub = ParagraphStyle(
        'PecIASub', parent=base['Normal'],
        fontSize=9, textColor=GRAY_MUTED, spaceAfter=12
    )
    return title, sub


def _build_doc(pdf_path, pagesize):
    return SimpleDocTemplate(
        pdf_path, pagesize=pagesize,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        topMargin=2.5 * cm, bottomMargin=1.5 * cm,
    )


def gerar_pdf_estoque_touros(db, reports_dir, logo_path,
                              grupo=None, apenas_disponiveis=True):
    where, params = [], []
    if grupo:
        where.append("eg.nome LIKE ?")
        params.append(f"%{grupo}%")
    if apenas_disponiveis:
        where.append("et.vendido = 0")
    sql = """
        SELECT et.brinco, COALESCE(eg.nome, '-') AS grupo, et.data_nasc,
               et.pai, et.iciagen, et.idesm, et.rmat, et.ifrig,
               et.peso, et.vendido
        FROM estoque_touros et
        LEFT JOIN estoque_grupos eg ON eg.id = et.grupo_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY et.iciagen IS NULL, et.iciagen DESC"
    rows = db.execute(sql, params).fetchall()

    os.makedirs(reports_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y-%m-%d_%Hh%M')
    filename = f'estoque-touros_{ts}.pdf'
    pdf_path = os.path.join(reports_dir, filename)

    doc = _build_doc(pdf_path, landscape(A4))
    title_style, sub_style = _styles()
    story = [Paragraph('Estoque de Touros para Venda', title_style)]

    filtros = []
    if grupo:
        filtros.append(f'Grupo: {grupo}')
    filtros.append('Apenas disponíveis' if apenas_disponiveis else 'Incluindo vendidos')
    n_disp = sum(1 for r in rows if not r['vendido'])
    n_vend = sum(1 for r in rows if r['vendido'])
    sub_line = (f"{' · '.join(filtros)}  |  Total: {len(rows)} touros  |  "
                f"Disponíveis: {n_disp}  |  Vendidos: {n_vend}")
    story.append(Paragraph(sub_line, sub_style))

    if not rows:
        story.append(Paragraph(
            'Nenhum touro encontrado com os filtros aplicados.', sub_style
        ))
    else:
        header = ['Brinco', 'Grupo', 'Nasc.', 'Pai',
                  'ICIAGen', 'IDESM', 'RMAT', 'IFRIG', 'Peso (kg)', 'Status']
        data = [header]
        for r in rows:
            data.append([
                r['brinco'] or '-',
                r['grupo'],
                r['data_nasc'] or '-',
                (r['pai'] or '-')[:18],
                _fmt(r['iciagen']),
                _fmt(r['idesm']),
                _fmt(r['rmat']),
                _fmt(r['ifrig']),
                _fmt(r['peso'], 1),
                'Vendido' if r['vendido'] else 'Disponível',
            ])
        col_widths = [2.4 * cm, 2.4 * cm, 1.8 * cm, 3.0 * cm,
                      1.7 * cm, 1.7 * cm, 1.7 * cm, 1.7 * cm,
                      1.9 * cm, 2.1 * cm]
        t = Table(data, colWidths=col_widths, repeatRows=1)
        t.setStyle(_table_style())
        story.append(t)

    chrome = _draw_chrome(logo_path)
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)

    size_bytes = os.path.getsize(pdf_path)
    return {
        'pdf_url': f'/relatorios/{filename}',
        'filename': filename,
        'size_kb': round(size_bytes / 1024, 1),
        'total_itens': len(rows),
        'tipo': 'estoque_touros',
    }


def gerar_pdf_estoque_embrioes(db, reports_dir, logo_path,
                                doadora=None, touro=None, apenas_disponiveis=True):
    where, params = [], []
    if doadora:
        where.append("doadora LIKE ?")
        params.append(f"%{doadora}%")
    if touro:
        where.append("touro LIKE ?")
        params.append(f"%{touro}%")
    if apenas_disponiveis:
        where.append("qtd_atual > 0")
    sql = ("SELECT id, dt_opu, dt_vitrificacao, doadora, touro, tipo_semen, "
           "qtd_inicial, qtd_atual, lab FROM embriao_lote")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY qtd_atual DESC, dt_vitrificacao DESC"
    rows = db.execute(sql, params).fetchall()

    os.makedirs(reports_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y-%m-%d_%Hh%M')
    filename = f'estoque-embrioes_{ts}.pdf'
    pdf_path = os.path.join(reports_dir, filename)

    doc = _build_doc(pdf_path, A4)
    title_style, sub_style = _styles()
    story = [Paragraph('Estoque de Embriões FIV', title_style)]

    filtros = []
    if doadora:
        filtros.append(f'Doadora: {doadora}')
    if touro:
        filtros.append(f'Touro: {touro}')
    filtros.append('Apenas com saldo' if apenas_disponiveis else 'Incluindo zerados')
    total_disp = sum((r['qtd_atual'] or 0) for r in rows)
    total_ini = sum((r['qtd_inicial'] or 0) for r in rows)
    sub_line = (f"{' · '.join(filtros)}  |  Lotes: {len(rows)}  |  "
                f"Disponíveis: {total_disp}  |  Produzidos: {total_ini}")
    story.append(Paragraph(sub_line, sub_style))

    if not rows:
        story.append(Paragraph(
            'Nenhum lote encontrado com os filtros aplicados.', sub_style
        ))
    else:
        header = ['Doadora', 'Touro', 'Sêmen', 'OPU',
                  'Vitrif.', 'Qtd Atual', 'Qtd Inicial', 'Lab']
        data = [header]
        for r in rows:
            data.append([
                (r['doadora'] or '-')[:18],
                (r['touro'] or '-')[:18],
                (r['tipo_semen'] or '-')[:8],
                r['dt_opu'] or '-',
                r['dt_vitrificacao'] or '-',
                str(r['qtd_atual'] or 0),
                str(r['qtd_inicial'] or 0),
                r['lab'] or '-',
            ])
        col_widths = [3.2 * cm, 3.2 * cm, 1.8 * cm, 2.1 * cm,
                      2.1 * cm, 1.8 * cm, 2.0 * cm, 1.8 * cm]
        t = Table(data, colWidths=col_widths, repeatRows=1)
        t.setStyle(_table_style())
        story.append(t)

    chrome = _draw_chrome(logo_path)
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)

    size_bytes = os.path.getsize(pdf_path)
    return {
        'pdf_url': f'/relatorios/{filename}',
        'filename': filename,
        'size_kb': round(size_bytes / 1024, 1),
        'total_itens': len(rows),
        'tipo': 'estoque_embrioes',
    }
