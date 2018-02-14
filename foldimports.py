import sublime
import sublime_plugin
import os

# SETTINGS START
fold_imports = True
# SETTINGS END

class FunctionsFoldImports(sublime_plugin.EventListener):
    def on_load(self, view):
        if not fold_imports or not isJavaFile(view):
            return
        import_statements = view.find_all(r'^(import|package)')
        if len(import_statements) > 0:
            start = view.line(import_statements[0]).begin() + 7
            end = view.line(import_statements[-1]).end()
            view.fold(sublime.Region(start, end))

def isJavaFile(view):
    fileName = view.file_name()
    if fileName == None:
        return False
    return fileName.endswith(".java")
