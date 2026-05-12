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


def _section_style():
    base = getSampleStyleSheet()
    return ParagraphStyle(
        'PecIASection', parent=base['Heading2'],
        fontSize=10.5, textColor=BRAND_BORDO,
        spaceBefore=14, spaceAfter=6, fontName='Helvetica-Bold'
    )


def _kv_table_style():
    """Tabela chave/valor — sem cabeçalho bordô, com zebra."""
    return TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('TEXTCOLOR', (0, 0), (0, -1), GRAY_MUTED),
        ('TEXTCOLOR', (2, 0), (2, -1), GRAY_MUTED),
        ('FONTSIZE', (0, 0), (0, -1), 8),
        ('FONTSIZE', (2, 0), (2, -1), 8),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.white, GRAY_ZEBRA]),
        ('BOX', (0, 0), (-1, -1), 0.25, GRAY_LINE),
    ])


def _ultima_rodada(db):
    return db.execute(
        "SELECT id, nome FROM rodadas ORDER BY data_import DESC LIMIT 1"
    ).fetchone()


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


def gerar_pdf_ranking_indice(db, reports_dir, logo_path,
                              indice='iciagen', categoria='TODAS',
                              limit=20, ordem='melhores'):
    rodada = _ultima_rodada(db)
    if not rodada:
        return {"erro": "Nenhuma rodada importada"}
    rid = rodada['id']

    indices_avaliacoes = {'iciagen', 'idesm', 'rmat', 'ifrig'}
    indices_matrizes = {'iep', 'ipp'}
    if indice not in indices_avaliacoes and indice not in indices_matrizes:
        return {"erro": f"Índice '{indice}' não suportado"}

    melhor_eh_menor = indice in {'iep', 'ipp'}
    direcao = 'ASC' if ((ordem == 'melhores') == melhor_eh_menor) else 'DESC'

    try:
        limit = max(1, min(int(limit or 20), 100))
    except (TypeError, ValueError):
        limit = 20

    cat_filter = ''
    params = [rid]
    if categoria and categoria != 'TODAS':
        cat_filter = " AND m.categoria = ?"
        params.append(categoria)

    if indice in indices_avaliacoes:
        sql = f"""
            SELECT m.animal_id, m.categoria, a.{indice} AS valor,
                   a.iciagen, m.touro_pai, m.mae_id
            FROM matrizes m
            JOIN avaliacoes a ON a.animal_id=m.animal_id AND a.rodada_id=m.rodada_id
            WHERE m.rodada_id=? AND m.ativo=1 AND a.{indice} IS NOT NULL{cat_filter}
            ORDER BY a.{indice} {direcao} LIMIT ?
        """
    else:
        sql = f"""
            SELECT m.animal_id, m.categoria, m.{indice} AS valor,
                   a.iciagen, m.touro_pai, m.mae_id
            FROM matrizes m
            LEFT JOIN avaliacoes a ON a.animal_id=m.animal_id AND a.rodada_id=m.rodada_id
            WHERE m.rodada_id=? AND m.ativo=1 AND m.{indice} IS NOT NULL{cat_filter}
            ORDER BY m.{indice} {direcao} LIMIT ?
        """
    params.append(limit)
    rows = db.execute(sql, params).fetchall()

    os.makedirs(reports_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y-%m-%d_%Hh%M')
    filename = f'ranking-{indice}_{ordem}_{ts}.pdf'
    pdf_path = os.path.join(reports_dir, filename)

    doc = _build_doc(pdf_path, A4)
    title_style, sub_style = _styles()
    story = []

    indice_label = {
        'iciagen': 'ICIAGen', 'idesm': 'IDESM', 'rmat': 'RMAT',
        'ifrig': 'IFRIG', 'iep': 'IEP', 'ipp': 'IPP'
    }.get(indice, indice.upper())
    cat_label = {
        'M': 'Matrizes (M)', 'N': 'Novilhas (N)',
        'TODAS': 'Matrizes + Novilhas'
    }.get(categoria, 'Todas')
    ordem_label = 'Melhores' if ordem == 'melhores' else 'Piores'
    story.append(Paragraph(
        f'Ranking — {ordem_label} {len(rows)} por {indice_label}', title_style
    ))
    story.append(Paragraph(
        f"Rodada: {rodada['nome']}  |  Categoria: {cat_label}", sub_style
    ))

    if not rows:
        story.append(Paragraph(
            'Nenhum animal encontrado para os filtros aplicados.', sub_style
        ))
    else:
        header = ['Pos', 'Brinco', 'Categ', indice_label, 'ICIAGen', 'Pai', 'Mãe']
        data = [header]
        casas = 2 if indice in indices_avaliacoes else 1
        for i, r in enumerate(rows, 1):
            data.append([
                str(i),
                r['animal_id'] or '-',
                r['categoria'] or '-',
                _fmt(r['valor'], casas),
                _fmt(r['iciagen']),
                (r['touro_pai'] or '-')[:14],
                (r['mae_id'] or '-')[:14],
            ])
        col_widths = [1.2 * cm, 2.8 * cm, 1.6 * cm, 2.2 * cm,
                      2.2 * cm, 3.2 * cm, 3.2 * cm]
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
        'tipo': 'ranking_indice',
    }


def gerar_pdf_ficha_animal(db, reports_dir, logo_path, brinco):
    brinco = (brinco or '').strip()
    if not brinco:
        return {"erro": "Brinco obrigatório"}
    rodada = _ultima_rodada(db)
    if not rodada:
        return {"erro": "Nenhuma rodada importada"}
    rid = rodada['id']

    m = db.execute("""
        SELECT m.animal_id, m.data_nasc, m.touro_pai, m.mae_id, m.avo_paterno, m.avo_materno,
               m.categoria, m.genotipada, m.precoce, m.ceip, m.ipp, m.iep, m.pv, m.desc_ap,
               a.iciagen, a.deca_icia_g, a.idesm, a.deca_idesm_g, a.rmat, a.deca_rmat, a.ifrig
        FROM matrizes m
        LEFT JOIN avaliacoes a ON a.animal_id=m.animal_id AND a.rodada_id=m.rodada_id
        WHERE m.animal_id = ? AND m.rodada_id = ? AND m.ativo = 1
    """, (brinco, rid)).fetchone()

    t_row = None
    p_row = None
    where_found = None
    sexo = '-'
    categoria_label = '-'
    animal_data = {}

    if m:
        where_found = 'Matriz Ativa'
        sexo = 'Fêmea'
        categoria_label = {'M': 'Matriz', 'N': 'Novilha'}.get(m['categoria'], m['categoria'] or '-')
        animal_data = dict(m)
    else:
        t_row = db.execute("""
            SELECT et.brinco, et.data_nasc, et.pai, et.avo_paterno, et.avo_materno,
                   et.idesm, et.iciagen, et.rmat, et.ifrig, et.peso, et.vendido,
                   eg.nome AS grupo
            FROM estoque_touros et
            LEFT JOIN estoque_grupos eg ON eg.id = et.grupo_id
            WHERE et.brinco = ?
        """, (brinco,)).fetchone()
        if t_row:
            where_found = 'Estoque de Venda — ' + ('Vendido' if t_row['vendido'] else 'Disponível')
            sexo = 'Macho'
            categoria_label = 'Touro (estoque comercial)'
            animal_data = dict(t_row)
        else:
            p_row = db.execute("""
                SELECT produto_id, mae_id, touro, sexo, data_nasc, pn, peso_desm,
                       idesm, iciagen, rmat
                FROM produtos WHERE produto_id = ? AND rodada_id = ?
            """, (brinco, rid)).fetchone()
            if p_row:
                where_found = 'Produto / Cria'
                sexo = {'M': 'Macho', 'F': 'Fêmea'}.get(p_row['sexo'], p_row['sexo'] or '-')
                categoria_label = 'Produto / Cria de safra'
                animal_data = dict(p_row)
            else:
                return {"erro": f"Animal '{brinco}' não encontrado em matrizes, estoque ou produtos."}

    os.makedirs(reports_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y-%m-%d_%Hh%M')
    safe_brinco = brinco.replace(' ', '-').replace('/', '-')
    filename = f'ficha-animal_{safe_brinco}_{ts}.pdf'
    pdf_path = os.path.join(reports_dir, filename)

    doc = _build_doc(pdf_path, A4)
    title_style, sub_style = _styles()
    section_style = _section_style()
    story = [
        Paragraph(f'Ficha do Animal — {brinco}', title_style),
        Paragraph(where_found, sub_style),
        Paragraph('Identificação', section_style),
    ]

    ident_data = [
        ['Brinco', animal_data.get('animal_id') or animal_data.get('brinco') or animal_data.get('produto_id') or brinco,
         'Sexo', sexo],
        ['Categoria', categoria_label,
         'Data Nascimento', animal_data.get('data_nasc') or '-'],
    ]
    if m:
        ident_data.append([
            'Peso Vivo',
            (_fmt(animal_data.get('pv'), 1) + ' kg') if animal_data.get('pv') else '-',
            'Genotipada', 'Sim' if animal_data.get('genotipada') else 'Não'
        ])
    elif t_row:
        ident_data.append([
            'Peso',
            (_fmt(animal_data.get('peso'), 1) + ' kg') if animal_data.get('peso') else '-',
            'Grupo/Safra', animal_data.get('grupo') or '-'
        ])
    ident_tbl = Table(ident_data, colWidths=[3.5 * cm, 5.5 * cm, 3.5 * cm, 5.5 * cm])
    ident_tbl.setStyle(_kv_table_style())
    story.append(ident_tbl)

    story.append(Paragraph('Genealogia', section_style))
    pai_key = 'touro_pai' if m else ('pai' if t_row else 'touro')
    geneal_data = [
        ['Pai', animal_data.get(pai_key) or '-',
         'Mãe', animal_data.get('mae_id') or '-'],
    ]
    if m or t_row:
        geneal_data.append([
            'Avô Paterno', animal_data.get('avo_paterno') or '-',
            'Avô Materno', animal_data.get('avo_materno') or '-'
        ])
    geneal_tbl = Table(geneal_data, colWidths=[3.5 * cm, 5.5 * cm, 3.5 * cm, 5.5 * cm])
    geneal_tbl.setStyle(_kv_table_style())
    story.append(geneal_tbl)

    has_indices = any(animal_data.get(k) is not None
                      for k in ('iciagen', 'idesm', 'rmat', 'ifrig'))
    if has_indices:
        story.append(Paragraph('Índices Genéticos', section_style))
        idx_data = [['Índice', 'Valor', 'DECA']]
        idx_rows = [
            ('ICIAGen', animal_data.get('iciagen'),
             animal_data.get('deca_icia_g') if m else None),
            ('IDESM', animal_data.get('idesm'),
             animal_data.get('deca_idesm_g') if m else None),
            ('RMAT', animal_data.get('rmat'),
             animal_data.get('deca_rmat') if m else None),
            ('IFRIG', animal_data.get('ifrig') if (m or t_row) else None, None),
        ]
        for label, valor, deca in idx_rows:
            if valor is not None:
                idx_data.append([label, _fmt(valor), str(deca) if deca else '-'])
        if len(idx_data) > 1:
            idx_tbl = Table(idx_data, colWidths=[5 * cm, 4 * cm, 4 * cm])
            idx_tbl.setStyle(_table_style())
            story.append(idx_tbl)

    if m:
        story.append(Paragraph('Reprodução', section_style))
        repro_data = [
            ['IPP',
             (_fmt(animal_data.get('ipp'), 1) + ' meses') if animal_data.get('ipp') else '-',
             'IEP',
             (_fmt(animal_data.get('iep'), 1) + ' meses') if animal_data.get('iep') else '-'],
            ['CEIP', 'Sim' if animal_data.get('ceip') else 'Não',
             'Precoce', 'Sim' if animal_data.get('precoce') else 'Não'],
        ]
        repro_tbl = Table(repro_data, colWidths=[3.5 * cm, 5.5 * cm, 3.5 * cm, 5.5 * cm])
        repro_tbl.setStyle(_kv_table_style())
        story.append(repro_tbl)

    if m and animal_data.get('desc_ap'):
        story.append(Paragraph('Observações — Aprumos', section_style))
        story.append(Paragraph(str(animal_data['desc_ap']), sub_style))

    chrome = _draw_chrome(logo_path)
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)

    size_bytes = os.path.getsize(pdf_path)
    return {
        'pdf_url': f'/relatorios/{filename}',
        'filename': filename,
        'size_kb': round(size_bytes / 1024, 1),
        'total_itens': 1,
        'tipo': 'ficha_animal',
    }


def gerar_pdf_panorama_rebanho(db, reports_dir, logo_path):
    rodada = _ultima_rodada(db)
    if not rodada:
        return {"erro": "Nenhuma rodada importada"}
    rid = rodada['id']

    kpis = db.execute("""
        SELECT COUNT(*) AS total_matrizes,
               AVG(a.iciagen) AS iciagen_avg,
               AVG(a.idesm) AS idesm_avg,
               AVG(a.rmat) AS rmat_avg,
               AVG(m.ipp) AS ipp_avg,
               AVG(m.iep) AS iep_avg,
               SUM(m.ceip) AS ceip_total,
               SUM(m.genotipada) AS genotipadas,
               SUM(m.precoce) AS precoces
        FROM matrizes m
        LEFT JOIN avaliacoes a ON a.animal_id=m.animal_id AND a.rodada_id=m.rodada_id
        WHERE m.rodada_id=? AND m.ativo=1
    """, (rid,)).fetchone()
    cats = db.execute("""
        SELECT categoria, COUNT(*) AS n FROM matrizes
        WHERE rodada_id=? AND ativo=1 GROUP BY categoria
    """, (rid,)).fetchall()
    emb = db.execute("""
        SELECT COUNT(*) AS n_lotes, COALESCE(SUM(qtd_atual),0) AS total
        FROM embriao_lote WHERE qtd_atual>0
    """).fetchone()
    estoque_t = db.execute(
        "SELECT COUNT(*) AS n FROM estoque_touros WHERE vendido=0"
    ).fetchone()

    os.makedirs(reports_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y-%m-%d_%Hh%M')
    filename = f'panorama-rebanho_{ts}.pdf'
    pdf_path = os.path.join(reports_dir, filename)

    doc = _build_doc(pdf_path, A4)
    title_style, sub_style = _styles()
    section_style = _section_style()
    story = [
        Paragraph(f'Panorama do Rebanho — {rodada["nome"]}', title_style),
        Paragraph('Snapshot da rodada mais recente importada.', sub_style),
        Paragraph('Plantel', section_style),
    ]

    matrizes_n = next((r['n'] for r in cats if r['categoria'] == 'M'), 0)
    novilhas_n = next((r['n'] for r in cats if r['categoria'] == 'N'), 0)
    plantel_data = [
        ['Matrizes ativas', str(kpis['total_matrizes'] or 0),
         'Categoria M', str(matrizes_n)],
        ['Categoria N (novilhas)', str(novilhas_n),
         'Com CEIP', str(kpis['ceip_total'] or 0)],
        ['Genotipadas', str(kpis['genotipadas'] or 0),
         'Precoces', str(kpis['precoces'] or 0)],
    ]
    plantel_tbl = Table(plantel_data, colWidths=[4.5 * cm, 4 * cm, 4.5 * cm, 4 * cm])
    plantel_tbl.setStyle(_kv_table_style())
    story.append(plantel_tbl)

    story.append(Paragraph('Índices Médios', section_style))
    idx_data = [['Índice', 'Valor médio']]
    idx_rows = [
        ('ICIAGen', kpis['iciagen_avg'], 2),
        ('IDESM', kpis['idesm_avg'], 2),
        ('RMAT', kpis['rmat_avg'], 2),
        ('IPP (meses)', kpis['ipp_avg'], 1),
        ('IEP (meses)', kpis['iep_avg'], 1),
    ]
    for label, valor, casas in idx_rows:
        if valor is not None:
            idx_data.append([label, _fmt(valor, casas)])
    idx_tbl = Table(idx_data, colWidths=[8 * cm, 6 * cm])
    idx_tbl.setStyle(_table_style())
    story.append(idx_tbl)

    story.append(Paragraph('Estoque', section_style))
    estoque_data = [
        ['Embriões disponíveis',
         f"{emb['total']} embriões em {emb['n_lotes']} lotes"],
        ['Touros à venda', f"{estoque_t['n']} disponíveis"],
    ]
    estoque_tbl = Table(estoque_data, colWidths=[5 * cm, 9 * cm])
    estoque_tbl.setStyle(_kv_table_style())
    story.append(estoque_tbl)

    chrome = _draw_chrome(logo_path)
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)

    size_bytes = os.path.getsize(pdf_path)
    return {
        'pdf_url': f'/relatorios/{filename}',
        'filename': filename,
        'size_kb': round(size_bytes / 1024, 1),
        'total_itens': kpis['total_matrizes'] or 0,
        'tipo': 'panorama_rebanho',
    }
