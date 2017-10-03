import sublime
import sublime_plugin
import os
import re

# SETTINGS START
use_getter_setter = True
getter_setter_ask = True

getter_setter_for_static_fields = True
getter_for_final_fields = True

instance_getter_setter_before_statics = True
getter_setter_before_inner_classes = True
# SETTINGS END

getter_setter_type = -1
cur_fp = None

g_temp = """{3}public {1} get{0}() {{
{3}    return {2};
{3}}}"""
s_temp = """{3}public void set{0}({1} {2}) {{
{3}    this.{2} = {2};
{3}}}"""
g_final_temp = """{3}public final {1} get{0}() {{
{3}    return {2};
{3}}}"""
g_stat_temp = """{3}public static {1} get{0}() {{
{3}    return {2};
{3}}}"""
s_stat_temp = """{3}public static void set{0}({1} _{2}) {{
{3}    {2} = _{2};
{3}}}"""
g_stat_final_temp = """{3}public static final {1} get{0}() {{
{3}    return {2};
{3}}}"""

java_field_pattern = "(?P<indent>\s*)" + \
        "(?P<access>protected|private)" + \
        "(?: (?P<transient>transient|volatile))?" + \
        "(?: (?P<static>static))?" + \
        "(?: (?P<final>final))?" + \
        "(?: (?P<type>[\w\<\>\,\.\s\[\]$]+))" + \
        "(?: (?P<name>[\w$]+))" + \
        "(?:\s*=.+)?;"
java_field_pattern = re.compile(java_field_pattern)

class JavaGetterSetterCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        global cur_fp
        sel = self.view.sel()[0]
        self.view.insert(edit, sel.end(), ';')
        if not use_getter_setter or not isJavaFile(self.view):
            return
        lineRegion = self.view.line(sel)
        line = self.view.substr(lineRegion)
        if 'private ' not in line:
            return
        if not getter_setter_for_static_fields and 'static ' in line:
            return
        if not getter_for_final_fields and 'final ' in line:
            return
        if sel.end() + 1 != lineRegion.end():
            return
        cur_fp = javaFieldPattern(line)
        if cur_fp.get('access', None) is None:
            return
        if getter_setter_ask:
            askOptions = []
            if cur_fp['final']:
                askOptions = [ 'Getter', 'Cancel' ]
            else:
                askOptions = [ 'Getter & Setter', 'Getter', 'Setter', 'Cancel' ]
            sublime.active_window().show_quick_panel(askOptions, self.onChosen)
        else:
            self.onChosen(0)

    def onChosen(self, index):
        global getter_setter_type
        getter_setter_type = index
        self.view.run_command('java_getter_setter_finish')

class JavaGetterSetterFinishCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        global getter_setter_type, cur_fp
        if getter_setter_type == -1 or getter_setter_type == 3 or cur_fp == None:
            return
        sel = self.view.sel()[0]
        lineRegion = self.view.line(sel)
        line = self.view.substr(lineRegion)
        if cur_fp['final'] and getter_setter_type == 1:
            return
        firstStatic = None
        firstClass = None
        firstInterface = None
        if instance_getter_setter_before_statics and cur_fp['static'] == None:
            firstStatic = self.view.line(self.view.find(r'\bstatic.*\(', self.view.line(sel).begin()))
        if getter_setter_before_inner_classes:
            firstClass = self.view.line(self.view.find(r'\bclass.*\{', self.view.line(sel).begin()))
            firstInterface = self.view.line(self.view.find(r'\binterface.*\{', self.view.line(sel).begin()))
        name = cur_fp['name']
        capName = name[0].capitalize() + name[1:len(name)]
        if cur_fp['final'] and name.isupper():
            capName = name.lower()
            capName = capName[0].capitalize() + capName[1:len(capName)]
        type = cur_fp['type']
        indent = cur_fp['indent']
        gArgs = []
        sArgs = []
        if cur_fp['static'] and cur_fp['final']:
            gArgs.append(g_stat_final_temp.format(capName, type, name, indent))
        elif cur_fp['static']:
            gArgs.append(g_stat_temp.format(capName, type, name, indent))
        elif cur_fp['final']:
            gArgs.append(g_final_temp.format(capName, type, name, indent))
        else:
            gArgs.append(g_temp.format(capName, type, name, indent))
        if not cur_fp['final']:
            if cur_fp['static']:
                sArgs.append(s_stat_temp.format(capName, type, name, indent))
            else:
                sArgs.append(s_temp.format(capName, type, name, indent))
        if len(gArgs) == 0 and len(sArgs) == 0:
            return
        for i in range(0, 4):
            lastLine = sublime.Region(self.view.size() - i, self.view.size() + 1 - i)
            if self.view.substr(lastLine).startswith('}'):
                break;
        insertPosition = lastLine.begin()
        if firstStatic == None or firstStatic.begin() == -1:
            firstStatic = lastLine
        if firstClass == None or firstClass.begin() == -1:
            firstClass = lastLine
        if firstInterface == None or firstInterface.begin() == -1:
            firstInterface = lastLine
        if firstStatic.begin() < insertPosition:
            insertPosition = firstStatic.begin()
        if firstClass.begin() < insertPosition:
            insertPosition = firstClass.begin()
        if firstInterface.begin() < insertPosition:
            insertPosition = firstInterface.begin()
        if insertPosition == -1:
            return
        methodsText = ''
        if not cur_fp['final'] and getter_setter_type == 0:
            if insertPosition == lastLine.begin():
                methodsText += '\n'
            methodsText += '' . join(gArgs) + '\n' + '\n' + '\n' . join(sArgs) + '\n'
            if insertPosition != lastLine.begin():
                methodsText += '\n'
        elif cur_fp['final'] or getter_setter_type == 1:
            if insertPosition == lastLine.begin():
                methodsText += '\n'
            methodsText += '' . join(gArgs) + '\n'
            if insertPosition != lastLine.begin():
                methodsText += '\n'
        elif getter_setter_type == 2:
            if insertPosition == lastLine.begin():
                methodsText += '\n'
            methodsText += '' . join(sArgs) + '\n'
            if insertPosition != lastLine.begin():
                methodsText += '\n'
        insertCount = self.view.insert(edit, insertPosition, methodsText)
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(insertPosition, (insertPosition + insertCount)))

def javaFieldPattern(line):
    m = java_field_pattern.match(line)
    if m:
        return m.groupdict()
    else:
        return {}

def isJavaFile(view):
    fileName = view.file_name()
    if fileName == None:
        return False
    return fileName.endswith(".java")
