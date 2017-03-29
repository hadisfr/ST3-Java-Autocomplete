import sublime
import sublime_plugin
import re
import os
import io
import zipfile
import collections

# SETTINGS START
class_cache_size = 8
add_getter_setter = True
getter_setter_before_statics = True
getter_setter_before_inner_classes = True
getter_for_final_fields = False
java_zip_archive_dir = '/lib/java-src.zip' # Appends project dir to beginning
# SETTINGS END

class_cache = collections.OrderedDict()

getter_template = """{3}public {1} get{0}() {{
{3}    return {2};
{3}}}"""

setter_template = """{3}public void set{0}({1} {2}) {{
{3}    this.{2} = {2};
{3}}}"""

java_field_pattern = "(?P<indent>\s*)" + \
        "(?P<access>protected|private)" + \
        "(?: (?P<transient>transient|volatile))?" + \
        "(?: (?P<static>static))?" + \
        "(?: (?P<final>final))?" + \
        "(?: (?P<type>[a-zA-Z0-9_$\<\>\,\.\s]+))" + \
        "(?: (?P<varname>[a-zA-Z0-9_$]+))" + \
        "(?:\s*=.+)?;"
java_field_pattern = re.compile(java_field_pattern)

completions = []

java_zip_archive = None
java_zip_file_names = None

class JavaGetterSetterCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        sel = self.view.sel()[0]
        lineRegion = self.view.line(sel)
        line = self.view.substr(lineRegion)
        if sel.end() != lineRegion.end():
            return
        result = javaFieldPattern(line)
        if result.get('access', None) is None or result.get('static', None) is not None:
            return
        firstStatic = None
        firstClass = None
        firstInterface = None
        if getter_setter_before_statics:
            firstStatic = self.view.line(self.view.find(r'\bstatic ', self.view.line(sel).begin()))
        if getter_setter_before_inner_classes:
            firstClass = self.view.line(self.view.find(r'\bclass ', self.view.line(sel).begin()))
            firstInterface = self.view.line(self.view.find(r'\binterface ', self.view.line(sel).begin()))
        getterArr = []
        setterArr = []
        fieldName = result['varname']
        capitalName = fieldName[0].capitalize() + fieldName[1:len(fieldName)]
        getterArr.append(getter_template.format(capitalName, result['type'], result['varname'], result['indent']))
        if not result['final']:
            setterArr.append(setter_template.format(capitalName, result['type'], result['varname'], result['indent']))
        if len(getterArr) == 0 and len(setterArr) == 0:
            return
        lastLine = self.view.line(self.view.size())
        if not self.view.substr(lastLine).startswith('}'):
            if not self.view.substr(self.view.line(self.view.size() - 1)).startswith('}'):
                lastLine = self.view.line(self.view.size() - 1)
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
        if not result['final']:
            methodsText = '' . join(getterArr) + '\n' + '\n' + '\n' . join(setterArr) + '\n' + '\n'
        else:
            methodsText = '' . join(getterArr) + '\n' + '\n'
        insertCount = self.view.insert(edit, insertPosition, methodsText)
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(insertPosition, (insertPosition + insertCount)))

class JavaAutocompletePeriodCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        sel = self.view.sel()[0]
        phrase = self.view.substr(self.view.word(sel.end()))
        word = self.view.word(sel.end() - 1)
        self.view.insert(edit, sel.end(), ".")
        if ')' in self.view.substr(sel.begin() - 1):
            word = prevWord(self.view, word)
        if ')' in phrase and '()' not in phrase:
            parens = findParensStart(self.view, sel.end() - 1)
            if parens != -1:
                num = sel.end() - parens
                word = self.view.word(sel.end() - num)
        object_types = autocompleteGetObjTypes(self.view, word)
        found1 = autocompleteAddFunctions(object_types[1])
        if found1 == False:
            found2 = autocompleteAddFunctions(object_types[0])
        if found1 or found2 or autocompleteAddFunctionsStatic(self.view.substr(word)):
            self.view.run_command('auto_complete', {
            'disable_auto_insert': True,
            'api_completions_only': True,
            'next_completion_if_showing': False
            })

class FunctionsAutoComplete(sublime_plugin.EventListener):
    def on_query_completions(self, view, prefix, locations):
        _completions = []
        line = view.substr(view.line(view.sel()[0]))
        checkPackage(view, line)
        checkImport(view, line)
        for c in list(set(completions)):
            c_snip = c
            params = re.findall('\w+\s+\w+(?=\)|,)',c_snip)
            num = 1
            for p in params:
                c_snip = c_snip.replace(p, '${' + str(num) + ':' + p + '}')
                num = num + 1
            c = c.replace(';', '')
            _completions.append((c, c_snip))
        del completions[:]
        return sorted(_completions)

    def on_modified(self, view):
        line = view.substr(view.line(view.sel()[0]))
        checkGettersSetters(view, line)

def loadJavaZip():
    global java_zip_archive, java_zip_file_names
    if java_zip_archive_dir == None:
        return
    if len(java_zip_archive_dir) == 0:
        return
    if java_zip_archive:
        return
    if java_zip_file_names:
        return
    projectBase = sublime.active_window().project_file_name()
    projectBase = projectBase[:projectBase.rfind('/')]
    java_zip_archive = zipfile.ZipFile(projectBase + java_zip_archive_dir)
    java_zip_file_names = java_zip_archive.namelist()

def readClass(className):
    fileName = findClass(className, True)
    if fileName != None and os.path.isfile(fileName):
        if fileName in class_cache:
            return class_cache[fileName]
        with open(fileName, 'r') as f:
            class_cache[fileName] = f.read()
            if len(class_cache) > class_cache_size:
                class_cache.popitem(False)
            return class_cache[fileName]
    fileName = findClassFromZip(className, True)
    if java_zip_archive != None and fileName != None:
        if fileName in class_cache:
            return class_cache[fileName]
        with java_zip_archive.open(fileName, 'r') as f:
            class_cache[fileName] = f.read().decode('utf-8').replace('\\n', '\n')
            if len(class_cache) > class_cache_size:
                class_cache.popitem(False)
            return class_cache[fileName]
    return ''

def findClass(className, exactMatch):
    for file in sublime.active_window().folders():
        fileNames = findClassesFromDir(file, className, exactMatch)
        if fileNames and len(fileNames) > 0:
            return fileNames[0]
    return findClassFromZip(className, exactMatch)

def findClasses(className, exactMatch):
    matches = []
    for file in sublime.active_window().folders():
        matches.extend(findClassesFromDir(file, className, exactMatch))
    matches.extend(findClassesFromZip(className, exactMatch))
    return matches

def findClassesFromDir(directory, className, exactMatch):
    matches = []
    if className == None:
        return matches
    classNameL = className.lower()
    for fileName in os.listdir(directory):
        fileName = os.path.join(directory, fileName)
        fileNameL = fileName.replace('\\', '/').lower()
        if os.path.isdir(fileName):
            foundClasses = findClassesFromDir(fileName, className, exactMatch)
            if foundClasses is not None:
                matches.extend(foundClasses)
        if not fileNameL.endswith('.java'):
            continue
        if exactMatch and fileNameL == classNameL:
            matches.append(fileName)
        if exactMatch and '/' in fileNameL and fileNameL[(fileNameL.rindex('/') + 1):-5] == classNameL:
            matches.append(fileName)
        if not exactMatch and '/' in fileNameL and classNameL in fileNameL[(fileNameL.rindex('/') + 1):-5]:
            matches.append(fileName)
        if not exactMatch and '/' in fileNameL and '/' in classNameL and classNameL in fileNameL:
            matches.append(fileName)
    return matches

def findClassFromZip(className, exactMatch):
    fileNames = findClassesFromZip(className, exactMatch)
    if fileNames and len(fileNames) > 0:
        return fileNames[0]

def findClassesFromZip(className, exactMatch):
    loadJavaZip()
    matches = []
    if java_zip_archive == None:
        return matches
    if className == None:
        return matches
    classNameL = className.lower()
    for fileName in java_zip_file_names:
        fileNameL = fileName.replace('\\', '/').lower()
        if not fileName.endswith('.java'):
            continue
        if exactMatch and fileNameL == classNameL:
            matches.append(fileName)
        if exactMatch and '/' in fileNameL and fileNameL[(fileNameL.rindex('/') + 1):-5] == classNameL:
            matches.append(fileName)
        if not exactMatch and '/' in fileNameL and classNameL in fileNameL[(fileNameL.rindex('/') + 1):-5]:
            matches.append(fileName)
        if not exactMatch and '/' in fileNameL and '/' in classNameL and classNameL in fileNameL:
            matches.append(fileName)
    return matches

def findPackages(packageName, exactMatch):
    matches = []
    for file in sublime.active_window().folders():
        matches.extend(findPackagesFrom(file, packageName, exactMatch))
    return matches

def findPackagesFrom(directory, packageName, exactMatch):
    matches = []
    packageNameL = packageName.lower()
    for fileName in os.listdir(directory):
        fileName = os.path.join(directory, fileName)
        fileNameL = fileName.replace('\\', '/').lower()
        if not os.path.isdir(fileName):
            continue
        if exactMatch and '/' in fileNameL and fileNameL[(fileNameL.rindex('/') + 1):] == packageNameL:
            matches.append(fileName)
        if not exactMatch and '/' in fileNameL and packageNameL in fileNameL[(fileNameL.rindex('/') + 1):]:
            matches.append(fileName)
        matches.extend(findPackagesFrom(fileName, packageName, exactMatch))
    return matches

def checkImport(view, line):
    if line.startswith('import '):
        input = view.substr(view.word(view.sel()[0].end()))
        input = line[7:].replace('.', '/')
        partialClasses = findClasses(input, False)
        for partialClass in partialClasses:
            partialClass = partialClass[:-5].replace('/', '.').replace('\\', '.')
            start_index = partialClass.find('src.')
            if start_index != -1:
                partialClass = partialClass[start_index + 4:]
            else:
                start_index = partialClass.find('source.')
                if start_index != -1:
                    partialClass = partialClass[start_index + 7:]
            completions.append(partialClass + ';')

def checkPackage(view, line):
    if line.startswith('package '):
        input = view.substr(view.word(view.sel()[0].end()))
        input = line[8:].replace('.', '/')
        partialPackages = findPackages(input, False)
        for partialPackage in partialPackages:
            partialPackage = partialPackage.replace('/', '.').replace('\\', '.')
            start_index = partialPackage.find('src.')
            if start_index != -1:
                partialPackage = partialPackage[start_index + 4:]
            else:
                start_index = partialPackage.find('source.')
                if start_index != -1:
                    partialPackage = partialPackage[start_index + 7:]
            completions.append(partialPackage + ';')

def checkGettersSetters(view, line):
    if not add_getter_setter:
        return
    if 'private ' not in line and 'protected ' not in line:
        return
    if ';' not in line:
        return
    if 'static ' in line:
        return
    if not getter_for_final_fields and 'final ' in line:
        return
    view.run_command('java_getter_setter')

def javaFieldPattern(line):
    m = java_field_pattern.match(line)
    if m:
        return m.groupdict()
    else:
        return {}

def isMethod(view, word):
    wordChecker = view.substr(word)
    wordPrev = view.substr(word.begin() - 1)
    if ' ' in wordPrev:
        return False
    if ')' in wordChecker:
        return True
    if '.' in wordChecker:
        return True
    if ')' in wordPrev:
        return True
    if '.' in wordPrev:
        return True
    return False

def findParensStart(view, endIndex):
    search_back = 100
    startIndex = endIndex - search_back
    if startIndex < 0:
        startIndex = 0
    fromString = view.substr(sublime.Region(startIndex, endIndex + 1))
    toret = {}
    pstack = []
    for i, c in enumerate(fromString):
        if c == '(':
            pstack.append(i)
        elif c == ')':
            if len(pstack) == 0:
                continue
            toret[i] = pstack.pop()
    if endIndex - startIndex in toret.keys():
        return startIndex + toret[endIndex - startIndex]
    return -1

def prevWord(view, word, num=1):
    if '.' in view.substr(word.begin() - 1) and ')' in view.substr(word.begin() - 2):
        parens = findParensStart(view, word.begin() - 2)
        if parens != -1:
            num = word.begin() - parens
        else:
            num = 3
    return view.word(word.begin() - num)

def nextWord(view, word, skip=1):
    return view.word(word.end() + skip)

def findMethods(fileName, staticOnly):
    if fileName == None:
        return []
    readData = readClass(fileName)
    methods = []
    if not readData:
        return methods
    if staticOnly:
        methodLines = re.findall('public static.*|protected static.*|static public.*|static protected.*', readData)
    else:
        methodLines = re.findall('public.*|protected.*', readData)
    for l in methodLines:
        s = re.search('(\w+)\s*\(.*\)(?=.*\{)', l)
        if not s:
            continue
        if '(' not in l:
            continue
        split = l[0:l.index('(')].split(' ')
        if len(split) < 3:
            continue
        methods.append(s.group().strip())
    comments = re.findall("/\*.*", readData)
    for c in comments:
        for m in methods:
            if m in c:
                methods.remove(m)
    superClass = re.search("extends\s*(\w*)", readData)
    if superClass:
        superClass = superClass.group()
        superClass = superClass[8:]
        for m in findMethods(findClass(superClass, True), staticOnly):
            methods.append(m)
    return methods

def autocompleteAddFunctions(className):
    if not className or len(className) == 0:
        return False
    classes = findClasses(className, True)
    for className in classes:
        methods = findMethods(className, False)
        if methods:
            for m in methods:
                completions.append(m)
    if classes and len(classes) > 0:
        return True
    return False

def autocompleteAddFunctionsStatic(className):
    if not className or len(className) == 0:
        return False
    methods = findMethods(findClass(className, True), True)
    if methods:
        for m in methods:
            completions.append(m)
    if methods:
        return True
    return False

def autocompleteGetReturnType(view, currentWord, methodRegion):
    method = view.substr(methodRegion)
    fileName = findClass(currentWord, True)
    if fileName == None:
        return None
    readData = readClass(fileName)
    returnType = re.search('([\w]+)(?=(?![\n\r]+)\s*' + re.escape(method) + ')', readData)
    if returnType == None:
        extend = re.search('extends\s*(\w+)', readData)
        return autocompleteGetReturnType(view, extend.group(1), methodRegion)
    returnType = returnType.group()
    return returnType

def autocompleteGetObjTypes(view, wordRegion):
    types = ['', '']
    previousWord = prevWord(view, wordRegion)
    if 'super' in view.substr(wordRegion):
        extend = view.find('extends', 0)
        types[0] = view.substr(nextWord(view, extend))
        return types
    if isMethod(view, wordRegion):
        prevObjType = autocompleteGetObjTypes(view, previousWord)
        objType = autocompleteGetReturnType(view, prevObjType[0], wordRegion)
        if prevObjType[0].startswith('<') and prevObjType[0].endswith('>'):
            types[0] = prevObjType[0][1:prevObjType[0].index('>')]
        else:
            types[0] = objType
        return types
    string = view.substr(wordRegion)
    regions = view.find_all('(?<![\\w])' + re.escape(string) + '\\b')
    for r in regions:
        previousWord = prevWord(view, r)
        if "storage.type" in view.scope_name(previousWord.begin()):
            if view.substr(previousWord) == '> ':
                types[0] = '<' + view.substr(prevWord(view, r, 2)) + '>'
            else:
                types[0] = view.substr(previousWord)
            nxtWord = nextWord(view, r, 7)
            line = view.substr(view.line(nxtWord))
            if ' = new ' in line:
                types[1] = view.substr(nxtWord)
            return types
    return None
