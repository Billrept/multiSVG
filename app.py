from flask import Flask, render_template, request, send_file, jsonify
import os
import xml.etree.ElementTree as ET
from zipfile import ZipFile
import cairosvg

app = Flask(__name__)
UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def convert_svg_to_png(svg_file, OUTPUT_PNG):
    cairosvg.svg2png(url=svg_file, write_to=OUTPUT_PNG)
    return OUTPUT_PNG

def separate_svg_layers(svg_file):
    tree = ET.parse(svg_file)
    root = tree.getroot()
    
    layers = {}
    
    for elem in root.iter():
        if 'id' in elem.attrib:
            elem_id = elem.attrib['id']
            if elem_id not in layers:
                layers[elem_id] = ET.Element('svg', attrib={'xmlns': 'http://www.w3.org/2000/svg'})
            layers[elem_id].append(elem)
    
    layer_paths = []
    for layer_id, layer_root in layers.items():
        layer_path = os.path.join(UPLOAD_FOLDER, f'{layer_id}.svg')
        layer_tree = ET.ElementTree(layer_root)
        layer_tree.write(layer_path, encoding='utf-8', xml_declaration=True)
        layer_paths.append((layer_id, layer_path))
    
    return layer_paths

def convert_svg_to_gcode(filepath, color):
    tree = ET.parse(filepath)
    root = tree.getroot()
    namespaces = {'svg': 'http://www.w3.org/2000/svg'}
    gcode_lines = []

    gcode_lines.append("G00 F4500.0\n")
    gcode_lines.append("Y0.000; !!Ybottom\n")
    gcode_lines.append("G00 F4500.0\n")
    gcode_lines.append("X0.000; !!Xleft\n")
    gcode_lines.append(f"G00 F4500.0 X0.000 Y0.000;\n")
    gcode_lines.append("M3 S45\n")

    for path in root.findall('.//svg:path', namespaces):
        d = path.attrib.get('d')
        if d:
            gcode_lines.extend(path_to_gcode(d, color))
    gcode_lines.append("\n")
    return gcode_lines

def path_to_gcode(path_data, color):
    gcode_lines = []
    commands = path_data.replace(',', ' ').split()
    gcode_lines.append(f"; Start of path for color {color}\n")

    current_command = None
    for item in commands:
        if item in 'MLC':
            current_command = item
        else:
            coordinates = list(map(float, item.split()))
            if current_command == 'M':
                x, y = coordinates
                gcode_lines.append(f"G00 X{x:.3f} Y{y:.3f}; move !!Xleft+{x:.3f} Ybottom+{y:.3f}\n")
            elif current_command == 'L':
                x, y = coordinates
                gcode_lines.append(f"G01 X{x:.3f} Y{y:.3f}; draw !!Xleft+{x:.3f} Ybottom+{y:.3f}\n")
            elif current_command == 'C':
                # Handle cubic Bezier curve (simplified example)
                x1, y1, x2, y2, x, y = coordinates
                gcode_lines.append(f"G01 X{x1:.3f} Y{y1:.3f}; draw !!Xleft+{x1:.3f} Ybottom+{y1:.3f}\n")
                gcode_lines.append(f"G01 X{x2:.3f} Y{y2:.3f}; draw !!Xleft+{x2:.3f} Ybottom+{y2:.3f}\n")
                gcode_lines.append(f"G01 X{x:.3f} Y{y:.3f}; draw !!Xleft+{x:.3f} Ybottom+{y:.3f}\n")
    gcode_lines.append(f"; End of path for color {color}\n")
    return gcode_lines

def process_svg_to_gcode(file):
    try:
        filepath = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(filepath)
        layers = separate_svg_layers(filepath)
        
        gcode_filepaths = []
        for color, layer_path in layers:
            gcode_lines = convert_svg_to_gcode(layer_path, color)
            gcode_filepath = os.path.join(UPLOAD_FOLDER, f'{color}.gcode')
            with open(gcode_filepath, 'w') as gcode_file:
                gcode_file.writelines(gcode_lines)
            gcode_filepaths.append(gcode_filepath)
        
        png_filepath = os.path.join(UPLOAD_FOLDER, 'output.png')
        convert_svg_to_png(filepath, png_filepath)
        
        zip_filepath = os.path.join(UPLOAD_FOLDER, 'files.zip')
        with ZipFile(zip_filepath, 'w') as zipf:
            for gcode_filepath in gcode_filepaths:
                zipf.write(gcode_filepath, os.path.basename(gcode_filepath))
            zipf.write(png_filepath, os.path.basename(png_filepath))
        
        return zip_filepath
    except Exception as e:
        app.logger.error(f"Error processing SVG to G-code: {e}")
        return None

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'svg_file' not in request.files:
            app.logger.error("No file part")
            return jsonify({'success': False, 'message': 'No file part'}), 400
        file = request.files['svg_file']
        if file.filename == '':
            app.logger.error("No selected file")
            return jsonify({'success': False, 'message': 'No selected file'}), 400
        zip_filepath = process_svg_to_gcode(file)
        if zip_filepath:
            return jsonify({'success': True, 'download_url': f'/download/{os.path.basename(zip_filepath)}'})
        else:
            app.logger.error("Error processing the file")
            return jsonify({'success': False, 'message': 'Error processing the file'}), 500
    return render_template('index.html')

@app.route('/download/<filename>')
def download_file(filename):
    try:
        return send_file(os.path.join(UPLOAD_FOLDER, filename), as_attachment=True)
    except Exception as e:
        app.logger.error(f"Error sending file {filename}: {e}")
        return "Error sending file", 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
