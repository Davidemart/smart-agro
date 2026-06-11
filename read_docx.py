import zipfile, xml.etree.ElementTree as ET, sys

try:
    z = zipfile.ZipFile(sys.argv[1])
    xml_content = z.read('word/document.xml')
    tree = ET.fromstring(xml_content)
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    texts = [node.text for node in tree.findall('.//w:t', ns) if node.text]
    print('\n'.join(texts))
except Exception as e:
    print(f"Error: {e}")
