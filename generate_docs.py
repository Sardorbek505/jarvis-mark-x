# -*- coding: utf-8 -*-
import os
import docx
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

output_dir = r"C:\Users\User\Desktop\Лабораторная_1_Эрметов"
os.makedirs(output_dir, exist_ok=True)

def build_student_doc():
    doc = docx.Document()
    
    # Landscape orientation and 2.5 cm margins
    sec1 = doc.sections[0]
    sec1.orientation = docx.enum.section.WD_ORIENT.LANDSCAPE
    # In python-docx, changing orientation requires swapping width/height
    sec1.page_width = Cm(29.7)
    sec1.page_height = Cm(21.0)
    sec1.top_margin = Cm(2.5)
    sec1.bottom_margin = Cm(2.5)
    sec1.left_margin = Cm(2.5)
    sec1.right_margin = Cm(2.5)

    # Title: Times New Roman, 26 pt, Bold, Center
    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_title.paragraph_format.space_before = Pt(0)
    p_title.paragraph_format.space_after = Pt(14)
    run_title = p_title.add_run("Классификация конфиденциальной информации")
    run_title.font.name = "Times New Roman"
    run_title.font.size = Pt(26)
    run_title.font.bold = True

    # Continuous section break for 2 columns
    sec2 = doc.add_section(docx.enum.section.WD_SECTION.CONTINUOUS)
    sec2.orientation = docx.enum.section.WD_ORIENT.LANDSCAPE
    sec2.page_width = Cm(29.7)
    sec2.page_height = Cm(21.0)
    sec2.top_margin = Cm(2.5)
    sec2.bottom_margin = Cm(2.5)
    sec2.left_margin = Cm(2.5)
    sec2.right_margin = Cm(2.5)

    # Configure XML for 2 unequal columns: col1 = 10 cm, col2 = 12 cm, space = 2.7 cm
    # In DXA: 1 cm = 567 dxa
    # col1 = 10.0 cm = 5670 dxa, space = 2.7 cm = 1530 dxa, col2 = 12.0 cm = 6804 dxa
    cols_xml = parse_xml(
        f'<w:cols {nsdecls("w")} w:num="2" w:equalWidth="0" w:space="1530">'
        f'<w:col w:w="5670" w:space="1530"/>'
        f'<w:col w:w="6804"/>'
        f'</w:cols>'
    )
    sec2._sectPr.append(cols_xml)

    # --- Column 1 text ---
    # Microsoft Sans Serif, 13 pt, single line spacing, indent 1 cm, justify
    p1 = doc.add_paragraph()
    p1.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p1.paragraph_format.line_spacing = 1.0
    p1.paragraph_format.first_line_indent = Cm(1.0)
    p1.paragraph_format.space_after = Pt(4)
    r1 = p1.add_run("Классификация тайн по шести категориям:")
    r1.font.name = "Microsoft Sans Serif"
    r1.font.size = Pt(13)

    items = [
        ("➢", "государственная тайна;"),
        ("●", "коммерческая тайна;"),
        ("◆", "банковская тайна;"),
        ("✱", "профессиональная тайна;"),
        ("✔", "служебная тайна;"),
        ("○", "персональные данные.")
    ]
    for bullet, item in items:
        p_item = doc.add_paragraph()
        p_item.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p_item.paragraph_format.line_spacing = 1.0
        p_item.paragraph_format.left_indent = Cm(1.0)
        p_item.paragraph_format.first_line_indent = Cm(-0.5)
        p_item.paragraph_format.space_after = Pt(2)
        
        rb = p_item.add_run(f"{bullet}  ")
        rb.font.name = "Arial"
        rb.font.size = Pt(11)
        
        ri = p_item.add_run(item)
        ri.font.name = "Microsoft Sans Serif"
        ri.font.size = Pt(13)

    p_end1 = doc.add_paragraph()
    p_end1.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p_end1.paragraph_format.line_spacing = 1.0
    p_end1.paragraph_format.first_line_indent = Cm(1.0)
    p_end1.paragraph_format.space_after = Pt(4)
    r_end1 = p_end1.add_run("Последние пять категорий составляют конфиденциальную информацию.")
    r_end1.font.name = "Microsoft Sans Serif"
    r_end1.font.size = Pt(13)

    # Column break to switch to Column 2
    p_break = doc.add_paragraph()
    run_break = p_break.add_run()
    run_break.add_break(docx.enum.text.WD_BREAK.COLUMN)

    # --- Column 2 text ---
    # Century Gothic, 14 pt, 1.5 line spacing, indent 1.25 cm, justify
    p2_1 = doc.add_paragraph()
    p2_1.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p2_1.paragraph_format.line_spacing = 1.5
    p2_1.paragraph_format.first_line_indent = Cm(1.25)
    p2_1.paragraph_format.space_after = Pt(6)

    def add_cg(p, text, underline=False):
        r = p.add_run(text)
        r.font.name = "Century Gothic"
        r.font.size = Pt(14)
        if underline:
            r.font.underline = True

    add_cg(p2_1, 'Слово "конфиденциальный" происходит от латинского ')
    add_cg(p2_1, 'confidentia', underline=True)
    add_cg(p2_1, ' - доверие и в современном русском языке означает "доверительный, не подлежащий огласке, секретный. Слово "секрет" заимствовано из французского ')
    add_cg(p2_1, 'secret', underline=True)
    add_cg(p2_1, ' - "тайна".')

    p2_2 = doc.add_paragraph()
    p2_2.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p2_2.paragraph_format.line_spacing = 1.5
    p2_2.paragraph_format.first_line_indent = Cm(1.25)
    p2_2.paragraph_format.space_after = Pt(6)
    add_cg(p2_2, 'Государственная тайна - защищаемые государством сведения в области его военной, внешнеполитической, экономической, разведывательной, контрразведывательной и оперативно-розыскной деятельности, распространение которых может нанести ущерб безопасности Российской Федерации (закон «О государственной тайне»).')

    # Footer setup
    for sec in [sec1, sec2]:
        footer = sec.footer
        p_f = footer.paragraphs[0]
        p_f.text = ""
        r_fl = p_f.add_run("Эрметов Сардорбек, группа ИБ-21, 09.09.2026")
        r_fl.font.name = "Times New Roman"
        r_fl.font.size = Pt(10)
        
        # Add tab and page number
        r_tab = p_f.add_run("\t\t1")
        r_tab.font.name = "Times New Roman"
        r_tab.font.size = Pt(10)

    return doc

# Save 3 versions
doc1 = build_student_doc()
path1 = os.path.join(output_dir, "Эрметов1.docx")
doc1.save(path1)

doc2 = build_student_doc()
path2 = os.path.join(output_dir, "Эрметов2.docx")
doc2.save(path2)

doc3 = build_student_doc()
path3 = os.path.join(output_dir, "Эрметов3.docx")
doc3.save(path3)

print("Created Эрметов1.docx, Эрметов2.docx, Эрметов3.docx successfully!")
