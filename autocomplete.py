import sublime
import sublime_plugin
import collections
import io
import os
import platform
import re
import subprocess
import zipfile

# SETTINGS START
class_cache_size = 64

java_library_completions = True
# Full path to the src.zip located in the JDK you're using (None to search for it)
java_library_path = None

show_static_methods = True
show_instance_fields = True
show_static_fields = True

override_class_autocompletes = {} # Prioritizing duplicate classnames
# Example 1: java.awt.List and java.util.List
# Example 2: java.util.Map methods aren't marked as public
override_class_autocompletes['List'] = 'ArrayList'
override_class_autocompletes['Map'] = 'HashMap'

max_open_file_search = 2048
max_file_search = 16384
# SETTINGS END

instanceMethodCompletions = []
instanceFieldCompletions = []
staticMethodCompletions = []
staticFieldCompletions = []
generalCompletions = []
java_zip_failed = False
java_zip_archive = None
java_zip_file_names = None
class_cache = collections.OrderedDict()

java_comment_pattern = re.compile(r'''((['"])(?:(?!\2|\\).|\\.)*\2)|\/\/[^\n]*|\/\*(?:[^*]|\*(?!\/))*\*\/''')
java_method_pattern = "(?:(protected|public|default)\s+)" + \
        "((?:(?:abstract|static|final|synchronized|native)\s+)*)" + \
        "(?:<[^>]*>\s+)?(\w+(?:\[\])?(?:<.*>)?)\s+" + \
        "(\w+)\s*" + \
        "\(\s*([^\)]*)\s*\)"
java_method_pattern = re.compile(java_method_pattern)
java_field_pattern = "(?:(protected|public|default)\s+)" + \
        "((?:(?:transient|volatile|static|final)\s+)*)" + \
        "(\w+(?:\[\])?(?:<\w+(?:,\s*\w+)?>?)?)\s+" + \
        "(\w+)\s*" + \
        "(?:\s*=\s*[^;]+)?;"
java_field_pattern = re.compile(java_field_pattern)
java_field_names_pattern = re.compile("(\w+)\s*(?:\s*=\s*[^;,]+)")
java_class_pattern = "(?:(protected|public|private|default)\s+)?" + \
        "((?:(?:abstract|static|final)\s+)*)" + \
        "(?:(class|interface|enum)\s+)" + \
        "(\w+(?:<\w+(?:,\s*\w+)?>?)?)" + \
        "(?:\s+extends\s+(\w+(?:<\w+(?:,\s*\w+)?>?)?))?" + \
        "(?:\s+implements\s+((?:(?:,\s*)*(?:\w+(?:<\w+(?:,\s*\w+)?>?)?))*))?"
java_class_pattern = re.compile(java_class_pattern)

class PeriodAutocompleteCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        sel = self.view.sel()[0]
        self.view.insert(edit, sel.end(), '.')
        if not isJavaFile(self.view):
            return
        findClassCompletions(self.view, sel)
        imLen = len(instanceMethodCompletions)
        ifLen = len(instanceFieldCompletions)
        smLen = len(staticMethodCompletions)
        sfLen = len(staticFieldCompletions)
        if imLen == 0 and smLen == 0 and ifLen == 0 and sfLen == 0:
            return
        self.view.run_command('hide_auto_complete')
        def show_auto_complete():
            self.view.run_command('auto_complete', {
                'disable_auto_insert': True,
                'api_completions_only': True,
                'next_completion_if_showing': False,
                'auto_complete_commit_on_tab': True
            })
        sublime.set_timeout(show_auto_complete, 0)

class ParensAutocompleteCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        sel = self.view.sel()[0]
        self.view.insert(edit, sel.end(), '(')
        if not isJavaFile(self.view):
            self.view.run_command('insert_snippet', {'contents': '$0)'})
            return
        classWord = self.view.word(sel.begin())
        if self.view.substr(classWord) == '>(':
            bracketPos = findStartBracket(self.view, classWord.begin(), '<>')
            if bracketPos != -1:
                classWord = self.view.word(bracketPos)
        if classWord is None:
            self.view.run_command('insert_snippet', {'contents': '$0)'})
            return
        newWord = self.view.word(classWord.begin() - 1)
        if newWord is None or self.view.substr(newWord) != 'new':
            self.view.run_command('insert_snippet', {'contents': '$0)'})
            return
        bufferedClass = getBufferedClass(self.view, self.view.substr(classWord))
        if bufferedClass is None:
            self.view.run_command('insert_snippet', {'contents': '$0)'})
            return
        for key, value in bufferedClass.constructors.items():
            if key is None or value is None:
                continue
            compArgs = methodArgsToCompletion(value)
            generalCompletions.append((key, compArgs + ')'))
        if len(generalCompletions) == 0:
            self.view.run_command('insert_snippet', {'contents': '$0)'})
            return
        self.view.run_command('hide_auto_complete')
        def show_auto_complete():
            self.view.run_command('auto_complete', {
                'disable_auto_insert': True,
                'api_completions_only': True,
                'next_completion_if_showing': False,
                'auto_complete_commit_on_tab': True
            })
        sublime.set_timeout(show_auto_complete, 0)

class FunctionsAutoComplete(sublime_plugin.EventListener):
    def on_query_completions(self, view, prefix, locations):
        _completions = []
        if not isJavaFile(view):
            return
        checkImport(view, view.substr(view.line(view.sel()[0])))
        if len(instanceMethodCompletions) > 0:
            _completions.extend(sorted(list(set(instanceMethodCompletions))))
        if len(instanceFieldCompletions) > 0:
            _completions.extend(sorted(list(set(instanceFieldCompletions))))
        if len(staticMethodCompletions) > 0:
            _completions.extend(sorted(list(set(staticMethodCompletions))))
        if len(staticFieldCompletions) > 0:
            _completions.extend(sorted(list(set(staticFieldCompletions))))
        if len(generalCompletions) > 0:
            _completions.extend(sorted(list(set(generalCompletions))))
        if len(_completions) == 0:
            return
        instanceMethodCompletions.clear()
        instanceFieldCompletions.clear()
        staticMethodCompletions.clear()
        staticFieldCompletions.clear()
        generalCompletions.clear()
        return _completions

class BufferedClass:
    def __init__(self, fn, md):
        self.fileName = fn
        self.modifiedDate = md
        self.outerClass = None
        self.accessModifier = None
        self.extends = None
        self.constructors = {}
        self.methods = {}
        self.fields = {}
        self.staticMethods = {}
        self.staticFields = {}
        self.innerClasses = {}

class ClassMethod:
    def __init__(self, n, t, a):
        self.name = n
        self.type = t
        self.args = a

class ClassField:
    def __init__(self, n, t, a):
        self.name = n
        self.type = t

def findEndBracket(text, bracketPos, brackets, missing = False):
    view = None
    if not isinstance(text, str):
        if bracketPos == -1:
            return -1
        view = text
        maxRange = min(bracketPos + max_open_file_search, view.size())
        viewStartOffset = bracketPos
        viewEndOffset = min(viewStartOffset + 64, view.size())
        text = view.substr(sublime.Region(viewStartOffset, viewEndOffset))
    else:
        maxRange = min(bracketPos + max_file_search, len(text))
    pstack = []
    textLength = len(text)
    for i in range(bracketPos, maxRange):
        if view is not None:
            textOffset = i - viewEndOffset
            if textOffset >= textLength:
                viewStartOffset = max(viewStartOffset + 64, textLength)
                viewEndOffset = max(viewEndOffset + 64, textLength)
                text = view.substr(sublime.Region(viewStartOffset, viewEndOffset))
                textLength = len(text)
                textOffset = i - viewEndOffset
            if textOffset >= textLength:
                break
            c = text[textOffset]
        else:
            c = text[i]
        if c == brackets[0]:
            pstack.append(i)
        elif c == brackets[1]:
            if len(pstack) == 0:
                if bracketPos == -1:
                    return i
                continue
            if bracketPos == pstack.pop():
                return i
    return -1

def findStartBracket(text, bracketPos, brackets, missing = False):
    view = None
    if not isinstance(text, str):
        if bracketPos == -1:
            return -1
        view = text
        minRange = max(bracketPos - max_open_file_search, -1)
        viewEndOffset = bracketPos
        viewStartOffset = max(viewEndOffset - 63, 0)
        text = view.substr(sublime.Region(viewStartOffset, viewEndOffset + 1))
    else:
        minRange = max(bracketPos - max_file_search, -1)
    pstack = []
    maxRange = bracketPos
    if bracketPos == -1:
        maxRange = len(text) - 1
        minRange = max(maxRange - max_file_search, -1)
    textLength = len(text)
    for i in range(maxRange, minRange, -1):
        if view is not None:
            textOffset = i - viewEndOffset + (textLength - 1)
            if textOffset < 0:
                viewStartOffset = max(viewStartOffset - 64, 0)
                viewEndOffset = max(viewEndOffset - 64, 0)
                text = view.substr(sublime.Region(viewStartOffset, viewEndOffset + 1))
                textLength = len(text)
                textOffset = i - viewEndOffset + (textLength - 1)
            if textOffset < 0:
                break
            c = text[textOffset]
        else:
            c = text[i]
        if c == brackets[1]:
            pstack.append(i)
        elif c == brackets[0]:
            if len(pstack) == 0:
                if bracketPos == -1:
                    return i
                continue
            if bracketPos == pstack.pop():
                return i
    return -1

def findClassCompletions(view, word):
    line = view.line(word.begin())
    lineBegin = line.begin()
    line = view.substr(line)
    line = line[:word.begin() - lineBegin]
    missingParensPos = findStartBracket(line, -1, '()')
    if missingParensPos != -1:
        line = line[missingParensPos + 1:]
    while True:
        bracketStart = line.rfind('(')
        if bracketStart == -1:
            break;
        bracketEnd = findEndBracket(line, bracketStart, '()')
        if bracketEnd == -1:
            break;
        line = line[:bracketStart] + line[bracketEnd + 1:]
    while True:
        bracketStart = line.rfind('[')
        if bracketStart == -1:
            break;
        bracketEnd = findEndBracket(line, bracketStart, '[]')
        if bracketEnd == -1:
            break;
        line = line[:bracketStart] + line[bracketEnd + 1:]
    while True:
        match = re.search('[^0-9a-zA-Z_$.]+', line)
        if match is None:
            break
        match = match.group()
        startPos = line.find(match)
        line = line[startPos + len(match):]
    line = line.strip()
    if line.find('this.') != -1: # Better ways to handle this than handing it back off to Sublime
        line = line[line.find('this.') + 5:]
    keys = line.split('.')
    if len(keys) == 0:
        return
    firstKey = keys[0]
    del keys[0]
    baseClass = getLocalClass(view, firstKey, word.begin())
    static = False
    if baseClass is None:
        baseClass = firstKey
        static = True
    currentClass = lastClass = fromClass = baseClass
    for index, key in enumerate(keys):
        currentClass = findKeyClass(view, currentClass, key)
        static = False
        if currentClass == 'E' or currentClass == 'V':
            if index == 0:
                currentClass = getLocalClass(view, firstKey, word.begin(), True)
            else:
                currentClass = findKeyClass(view, fromClass, keys[index - 1], True)
        fromClass = lastClass
        lastClass = currentClass
    addClassCompletions(view, getBufferedClass(view, currentClass), static)

def getLocalClass(view, key, maxPos, classType = False):
    if key == 'super':
        className = getClassName(view.file_name())
        bufferedClass = getBufferedClass(view, className)
        if bufferedClass is None or bufferedClass.extends is None:
            return None
        return bufferedClass.extends
    regions = view.find_all('(?<![\\w])' + re.escape(key) + '\\b')
    for region in reversed(regions):
        if region.begin() > maxPos:
            continue
        if 'new' not in view.substr(view.word(region.end() + 3)):
            classWord = view.word(region.begin() - 1)
            classWordString = re.escape(view.substr(classWord))
            regionString = re.escape(view.substr(region))
            wholeString = view.substr(sublime.Region(classWord.begin(), region.end()))
            if re.search('[^([]\s+' + regionString, wholeString) is None:
                continue
        else:
            classWord = view.word(region.end() + 7)
            if classType:
                bracketPos = findEndBracket(view, classWord.end(), '<>')
                if bracketPos != -1:
                    classWord = view.word(bracketPos)
        if view.substr(classWord) == '[] ':
            classWord = view.word(region.begin() - 3)
        if view.substr(classWord) == '> ':
            if classType:
                classWord = view.word(region.begin() - 2)
            else:
                bracketPos = findStartBracket(view, classWord.begin(), '<>')
                if bracketPos != -1:
                    classWord = view.word(bracketPos)
        scopeName = view.scope_name(classWord.begin())
        if classWord is not None and 'storage.type' in scopeName:
            return view.substr(classWord)
        if classWord is not None and 'constant.other' in scopeName:
            return view.substr(classWord)
    return findKeyClass(view, getClassName(view.file_name()), key, classType)

def findKeyClass(view, className, key, classType = False):
    if not key.endswith('('):
        key += '('
    origKey = key[:-1]
    bufferedClass = getBufferedClass(view, className)
    if bufferedClass is None:
        return None
    if bufferedClass.accessModifier == 'private':
        editingClass = getClassName(view.file_name())
        if editingClass != className and editingClass != bufferedClass.outerClass:
            return None
    for _key, _value in bufferedClass.methods.items():
        if _key is None or _value is None:
            continue
        if key in _key and _value.type is not None:
            type = _value.type.replace('[]', '')
            if '<' in type:
                if classType:
                    type = type[type.find('<') + 1:type.find('>')]
                    if type.find(',') != -1:
                        type = type[type.find(',') + 1:].strip()
                else:
                    type = type[:type.find('<')]
            return type
    for _key, _value in bufferedClass.staticMethods.items():
        if _key is None or _value is None:
            continue
        if key in _key and _value.type is not None:
            if '<' in type:
                if classType:
                    type = type[type.find('<') + 1:type.find('>')]
                    if type.find(',') != -1:
                        type = type[type.find(',') + 1:].strip()
                else:
                    type = type[:type.find('<')]
            return type
    for _key, _value in bufferedClass.fields.items():
        if _key is None or _value is None:
            continue
        if origKey == _key and _value is not None:
            type = _value.replace('[]', '')
            if '<' in type:
                if classType:
                    type = type[type.find('<') + 1:type.find('>')]
                    if type.find(',') != -1:
                        type = type[type.find(',') + 1:].strip()
                else:
                    type = type[:type.find('<')]
            return type
    for _key, _value in bufferedClass.staticFields.items():
        if _key is None or _value is None:
            continue
        if origKey == _key and _value is not None:
            type = _value.replace('[]', '')
            if '<' in type:
                if classType:
                    type = type[type.find('<') + 1:type.find('>')]
                    if type.find(',') != -1:
                        type = type[type.find(',') + 1:].strip()
                else:
                    type = type[:type.find('<')]
            return type
    if bufferedClass.extends is not None:
        return findKeyClass(view, bufferedClass.extends, key)
    else:
        return None

def addClassCompletions(view, bufferedClass, staticOnly):
    if bufferedClass is None:
        return False
    if not staticOnly:
        for key, value in bufferedClass.methods.items():
            if key is None or value is None:
                continue
            if len(key) > 48:
                key = key[:48].strip() + '...'
            compArgs = methodArgsToCompletion(value.args)
            instanceMethodCompletions.append((key + '\t' + value.type, value.name + '(' + compArgs + ')'))
        if show_instance_fields:
            for key, value in bufferedClass.fields.items():
                if key is None or value is None:
                    continue
                if len(key) > 48:
                    key = key[:48].strip() + '...'
                instanceFieldCompletions.append((key + '\t' + value, key))
    if show_static_methods:
        for key, value in bufferedClass.staticMethods.items():
            if key is None or value is None:
                continue
            if len(key) > 48:
                key = key[:48].strip() + '...'
            compArgs = methodArgsToCompletion(value.args)
            staticMethodCompletions.append((key + '\t' + value.type, value.name + '(' + compArgs + ')'))
    if show_static_fields:
        for key, value in bufferedClass.staticFields.items():
            if key is None or value is None:
                continue
            if len(key) > 48:
                key = key[:48].strip() + '...'
            staticFieldCompletions.append((key + '\t' + value, key))
    if bufferedClass.extends is not None:
        addClassCompletions(view, getBufferedClass(view, bufferedClass.extends), staticOnly)
    return True

def methodArgsToCompletion(args):
    argsList = re.findall('[^,\s][^,]+\<[^>]+>[^,\n]*|[^,\s][^,\n]+', args)
    num = 1
    for arg in argsList:
        args = args.replace(arg, '${' + str(num) + ':' + arg + '}')
        num = num + 1
    return args

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
    if className is None:
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
    if java_zip_archive is None:
        return matches
    if className is None:
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

def loadJavaZip():
    global java_zip_failed, java_zip_archive, java_zip_file_names
    if not java_library_completions or java_zip_failed or java_zip_archive or java_zip_file_names:
        return
    javaPath = None
    whichPath = java_library_path
    if java_library_path is None:
        paths = [ 'javac', 'java', 'javac.exe', 'java.exe' ]
        for path in paths:
            path = which(path)
            if path is not None:
                whichPath = path
                break;
    if whichPath is None:
        java_zip_failed = True
        return
    whichPath = os.path.dirname(whichPath)
    if os.path.isfile(os.path.join(whichPath, 'src.zip')):
        javaPath = os.path.abspath(os.path.join(whichPath, 'src.zip'))
    elif os.path.isfile(os.path.join(whichPath, '../src.zip')):
        javaPath = os.path.abspath(os.path.join(whichPath, '../src.zip'))
    if javaPath is None and platform.system() == 'Darwin':
        whichPath = subprocess.check_output('echo $(/usr/libexec/java_home)', shell = True).decode()
        whichPath = whichPath.replace('\n', '')
        if os.path.isfile(os.path.join(whichPath, 'src.zip')):
            javaPath = os.path.abspath(os.path.join(whichPath, 'src.zip'))
        elif os.path.isfile(os.path.join(whichPath, '../src.zip')):
            javaPath = os.path.abspath(os.path.join(whichPath, '../src.zip'))
    if javaPath is None:
        java_zip_failed = True
        return
    java_zip_archive = zipfile.ZipFile(javaPath)
    java_zip_file_names = java_zip_archive.namelist()

def which(search = None):
    if search:
        (path, name) = os.path.split(search)
        if os.access(search, os.X_OK):
            return search
        for path in os.environ.get('PATH').split(os.pathsep):
            fullPath = os.path.join(path, search)
            if os.access(fullPath, os.X_OK):
                return fullPath
    return None

def getBufferedClass(view, className):
    if className is None:
        return None
    subClassName = None
    if '$' in className:
        indexof = className.find('$')
        subClassName = className[indexof + 1:]
        className = className[:indexof]
    if className in override_class_autocompletes:
        className = override_class_autocompletes[className]
    matchedBufferedClass = None
    if className in class_cache:
        bufferedClass = class_cache[className]
        md = bufferedClass.modifiedDate
        if md == 0 or subClassName is not None or md == os.path.getmtime(bufferedClass.fileName):
            matchedBufferedClass = bufferedClass
    if matchedBufferedClass is None:
        fileName = findClass(className, True)
        if fileName is not None and os.path.isfile(fileName):
            fileDate = os.path.getmtime(fileName)
            with open(fileName, 'r') as f:
                matchedBufferedClass = addBufferedClass(fileName, f.read())
        else:
            fileName = findClassFromZip(className, True)
            if java_zip_archive is not None and fileName is not None:
                with java_zip_archive.open(fileName, 'r') as f:
                    fileData = f.read().decode('utf-8').replace('\\n', '\n')
                    matchedBufferedClass = addBufferedClass(fileName, fileData)
    if matchedBufferedClass is not None and subClassName is not None:
        if subClassName in matchedBufferedClass.innerClasses:
            return matchedBufferedClass.innerClasses[subClassName]
        matchedBufferedClass = None
    editingClass = getClassName(view.file_name())
    if matchedBufferedClass is None and className != editingClass and subClassName is None:
        return getBufferedClass(view, editingClass + '$' + className)
    else:
        return matchedBufferedClass

def addBufferedClass(fileName, fileData):
    className = getClassName(fileName)
    if os.path.isfile(fileName):
        bufferedClass = BufferedClass(fileName, os.path.getmtime(fileName))
    else:
        bufferedClass = BufferedClass(fileName, 0)
    if '$' in fileName:
        bufferedClass.outerClass = fileName[:fileName.find('$')]
    for match in java_comment_pattern.finditer(fileData):
        group0 = match.group(0)
        group1 = match.group(1)
        group2 = match.group(2)
        if group1 is not None or group2 is not None:
            continue
        fileData = fileData.replace(group0, '', 1)
    innerClasses = java_class_pattern.finditer(fileData)
    next(innerClasses)
    for innerClass in innerClasses:
        fullGroup = innerClass.group()
        if len(fullGroup) == 0:
            continue
        innerClassName = innerClass.group(4)
        indexofInner = fileData.find(fullGroup)
        indexofBracket = fileData.find('{', indexofInner)
        foundBracket = findEndBracket(fileData, indexofBracket, '{}')
        if foundBracket == -1:
            continue
        innerClass = fileData[indexofInner:foundBracket + 1]
        fileName = className + '$' + innerClassName
        bufferedClass.innerClasses[innerClassName] = addBufferedClass(fileName, innerClass)
        fileData = fileData[:indexofInner] + fileData[foundBracket + 1:]
    classInfo = java_class_pattern.search(fileData)
    if classInfo is not None:
        bufferedClass.accessModifier = classInfo.group(1)
        bufferedClass.extends = classInfo.group(5)
    cr = '(?:(protected|public|default)\s+)(' + re.escape(className) + ')\s*\(\s*([^\)]*)\s*\)';
    while True:
        constructor = re.search(cr, fileData)
        if constructor is None:
            break
        fullGroup = constructor.group()
        if len(fullGroup) == 0:
            break
        constructorName = constructor.group(2)
        constructorArgs = constructor.group(3)
        constructorArgs = constructorArgs.replace('\n', '')
        constructorArgs = constructorArgs.replace('\r', '')
        constructorArgs = re.sub('\s\s+', ' ', constructorArgs)
        fullName = constructorName + '(' + constructorArgs + ')'
        bufferedClass.constructors[fullName] = constructorArgs
        constructorPos = fileData.find(constructor.group())
        bracketStartPos = fileData.find('{', constructorPos)
        if bracketStartPos == -1:
            fileData = fileData[:constructorPos] + fileData[constructorPos + len(fullGroup) + 1:]
            continue
        bracketEndPos = findEndBracket(fileData, bracketStartPos, '{}')
        if bracketEndPos == -1:
            fileData = fileData[:constructorPos] + fileData[constructorPos + len(fullGroup) + 1:]
            continue
        fileData = fileData[:constructorPos] + fileData[bracketEndPos + 1:]
    while True:
        method = java_method_pattern.search(fileData)
        if method is None:
            break
        fullGroup = method.group()
        if len(fullGroup) == 0:
            break
        methodName = method.group(4)
        methodArgs = method.group(5)
        methodArgs = methodArgs.replace('\n', '')
        methodArgs = methodArgs.replace('\r', '')
        methodArgs = re.sub('\s\s+', ' ', methodArgs)
        fullName = methodName + '(' + methodArgs + ')'
        keywords = method.group(2).strip()
        if 'static' in keywords:
            bufferedClass.staticMethods[fullName] = ClassMethod(methodName, method.group(3), methodArgs)
        else:
            bufferedClass.methods[fullName] = ClassMethod(methodName, method.group(3), methodArgs)
        methodPos = fileData.find(fullGroup)
        if 'abstract' in keywords:
            semicolonPos = fileData.find(';', methodPos)
            if semicolonPos == -1:
                fileData = fileData[:methodPos] + fileData[methodPos + len(fullGroup) + 1:]
                continue
            fileData = fileData[:methodPos] + fileData[semicolonPos + 1:]
        else:
            bracketStartPos = fileData.find('{', methodPos)
            if bracketStartPos == -1:
                fileData = fileData[:methodPos] + fileData[methodPos + len(fullGroup) + 1:]
                continue
            bracketEndPos = findEndBracket(fileData, bracketStartPos, '{}')
            if bracketEndPos == -1:
                fileData = fileData[:methodPos] + fileData[methodPos + len(fullGroup) + 1:]
                continue
            fileData = fileData[:methodPos] + fileData[bracketEndPos + 1:]
    fields = java_field_pattern.finditer(fileData)
    for field in fields:
        keywords = field.group(2).strip()
        type = field.group(3)
        fieldNames = java_field_names_pattern.finditer(field.group())
        for fieldName in fieldNames:
            if 'static' in keywords:
                bufferedClass.staticFields[fieldName.group(1)] = type
            else:
                bufferedClass.fields[fieldName.group(1)] = type
    if '$' not in fileName:
        class_cache[className] = bufferedClass
        if len(class_cache) > class_cache_size:
            class_cache.popitem(False)
    return bufferedClass

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
            name = partialClass
            path = ''
            if '.' in name:
                path = name[:name.rfind('.')]
                name = name[name.rfind('.') + 1:]
            generalCompletions.append((name + '\t' + path, partialClass + ';'))

def getClassName(fileName):
    if fileName.rfind('/') != -1:
        fileName = fileName[fileName.rfind('/') + 1:]
    if fileName.rfind('\\') != -1:
        fileName = fileName[fileName.rfind('\\') + 1:]
    if fileName.rfind('$') != -1:
        fileName = fileName[fileName.rfind('$') + 1:]
    if fileName.find('.') != -1:
        fileName = fileName[:fileName.rfind('.')]
    return fileName

def isJavaFile(view):
    fileName = view.file_name()
    if fileName == None:
        return False
    return fileName.endswith(".java")
