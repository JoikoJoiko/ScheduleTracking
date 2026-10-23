"""Small, dependency-free OOXML writer for schedule matrices."""
import io
import textwrap
from copy import deepcopy
from zipfile import ZipFile,ZIP_DEFLATED
from xml.etree import ElementTree as ET

# DOCX is a small OOXML package; no desktop Office or converter is needed on the server.
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
ET.register_namespace('w',W);ET.register_namespace('r',R)


def el(name,attrs=None,text=None):
    node=ET.Element('{'+W+'}'+name,{'{'+W+'}'+k:str(v) for k,v in (attrs or {}).items()})
    if text is not None:node.text=str(text)
    return node


def paragraph(text,style=None,bold=False,compact=False):
    p=el('p');props=el('pPr')
    if style:props.append(el('pStyle',{'val':style}))
    props.append(el('spacing',{'after':0 if compact else 20,'before':0,'line':240,'lineRule':'auto'}))
    p.append(props)
    for i,line in enumerate(str(text).split('\n')):
        run=el('r')
        if bold:
            rp=el('rPr');rp.append(el('b'));run.append(rp)
        if i:run.append(el('br'))
        t=el('t',text=line);t.set('{http://www.w3.org/XML/1998/namespace}space','preserve');run.append(t);p.append(run)
    return p


def word_document(report):
    fit=all(s['headers'][0]=='Пара' for s in report['sections'])
    if fit:
        report=deepcopy(report)
        for s in report['sections']:
            s['rows']=[[str(cell).replace('\n',' · ') for cell in row] for row in s['rows']]
    size=18
    if fit:
        # Estimate wrapped row heights, then leave room for Word's font/layout differences.
        for size in range(18,3,-1):
            height=90
            for s in report['sections']:
                height+=22 if s['title'] else 5
                columns=len(s['headers']);widths=[55]+[719/(columns-1)]*(columns-1)
                for row in [s['headers'],*s['rows']]:
                    lines=max(sum(max(1,len(textwrap.wrap(line,width=max(1,int((w-4)/(size/2*.56))))))
                        for line in str(cell).split('\n')) for w,cell in zip(widths,row))
                    height+=lines*size/2*1.2+4
            if height<490:break
    document=el('document');body=el('body');document.append(body)
    body.extend([paragraph(report['title'],'Title'),paragraph(report['subtitle'],'Subtitle')])
    for index,section in enumerate(report['sections']):
        if section['title']:
            p=paragraph(section['title'],'Heading1')
            if index and not fit:p.find('{'+W+'}pPr').insert(1,el('pageBreakBefore'))
            body.append(p)
        table=el('tbl');props=el('tblPr');props.append(el('tblW',{'w':15478,'type':'dxa'}))
        borders=el('tblBorders')
        for side in ['top','left','bottom','right','insideH','insideV']:
            borders.append(el(side,{'val':'single','sz':6,'color':'D9D9D9'}))
        props.append(borders);props.append(el('tblLayout',{'type':'fixed'}));margins=el('tblCellMar')
        for side in ['top','left','bottom','right']:margins.append(el(side,{'w':30 if fit else 65,'type':'dxa'}))
        props.append(margins);table.append(props)
        columns=len(section['headers']);first=1100 if section['headers'][0]=='Пара' else 1550
        widths=[first]+[(15478-first)//(columns-1)]*(columns-1)
        grid=el('tblGrid')
        for width in widths:grid.append(el('gridCol',{'w':width}))
        table.append(grid)
        for row_index,cells in enumerate([section['headers'],*section['rows']]):
            tr=el('tr');rp=el('trPr');rp.append(el('cantSplit'))
            if row_index==0:rp.append(el('tblHeader'))
            tr.append(rp)
            for width,text in zip(widths,cells):
                tc=el('tc');cp=el('tcPr');cp.append(el('tcW',{'w':width,'type':'dxa'}))
                if row_index==0:cp.append(el('shd',{'fill':'DCE6F1'}))
                cp.append(el('vAlign',{'val':'center'}))
                tc.append(cp);tc.append(paragraph(text,'ScheduleCell' if fit else None,bold=row_index==0,compact=fit));tr.append(tc)
            table.append(tr)
        body.append(table);body.append(paragraph('','ScheduleCell' if fit else None,compact=fit))
    section=el('sectPr');section.extend([el('footerReference',{'type':'default'}),el('pgSz',{'w':16838,'h':11906,'orient':'landscape'}),
        el('pgMar',{'top':600,'bottom':600,'left':680,'right':680,'header':300,'footer':300,'gutter':0})])
    section[0].set('{'+R+'}id','rIdFooter');body.append(section)
    styles=el('styles');defaults=el('docDefaults');rdefault=el('rPrDefault');rp=el('rPr')
    rp.extend([el('rFonts',{'ascii':'Arial','hAnsi':'Arial','cs':'Arial'}),el('color',{'val':'000000'}),el('sz',{'val':18}),el('lang',{'val':'ru-RU'})])
    rdefault.append(rp);defaults.append(rdefault);styles.append(defaults)
    for name,size in [('Normal',18),('Title',26 if fit else 32),('Subtitle',16 if fit else 20),('Heading1',18 if fit else 25),('ScheduleCell',size)]:
        style=el('style',{'type':'paragraph','styleId':name});style.append(el('name',{'val':name}));props=el('rPr')
        props.extend([el('color',{'val':'000000'}),el('sz',{'val':size})]);style.append(props);styles.append(style)
    footer=el('ftr');p=paragraph('');p.find('{'+W+'}pPr').append(el('jc',{'val':'center'}));field=el('fldSimple',{'instr':'PAGE'})
    run=el('r');run.append(el('t',text='1'));field.append(run);p.append(field);footer.append(p)
    contenttypes='''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/footer.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/></Types>'''
    rels='''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'''
    docrels='''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer.xml"/></Relationships>'''
    output=io.BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as archive:
        for path,data in {'[Content_Types].xml':contenttypes,'_rels/.rels':rels,'word/_rels/document.xml.rels':docrels,
                          'word/document.xml':ET.tostring(document,encoding='utf-8',xml_declaration=True),
                          'word/styles.xml':ET.tostring(styles,encoding='utf-8',xml_declaration=True),
                          'word/footer.xml':ET.tostring(footer,encoding='utf-8',xml_declaration=True)}.items():archive.writestr(path,data)
    return output.getvalue()
