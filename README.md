# ST3-Java-Autocomplete
Autocompletion for Sublime Text 3  
  
This package will use your project's configured folders to give you autocompletions for your own Java code. You can configure it to include Java's own source to further give you autocompletions. This can also create getters and setters at the bottom of your class to save you time.  
  
Go to preferences -> Browse Packages  
Create a folder named Java-AutoComplete  
Add the files from this git into the folder.  
  
  
autofill.sublime-completions is an experimental file to help with some basic autocompletion and might be more annoying than useful. You can safely skip adding this file to the package folder for this plugin.  
  
  
The settings are currently all handled directory in the Python (autocomplete.py) file.  
  
  
class_cache_size: number of files to keep in memory for autocompletion. The higher the number, the faster things will go, but at a cost for memory.  
  
add_getter_setter: automatically add getters and setters at the bottom of your class when you add a field (triggered when you type a semicolon to complete the addition of a field). CURRENTLY DOES NOT CHECK FOR EXISTING GETTER/SETTER, so be careful modifying an existing field declaration.  
  
getter_setter_before_statics: add getters and setters above static methods (assuming you put static methods at the bottom of your classes).  
  
getter_setter_before_inner_classes: add getters and setters above inner classes (assuming you put inner classes at the bottom of your classes).  
  
getter_for_final_fields: whether or not to add getters for final fields.  
  
java_zip_archive_dir: zip archive of the java sourcecode, used for autocompletion. If you want autocompletion for Java's library, you will have to include the src.zip that comes with most (all?) Java JDK installations. Specifically, this src.zip is inside the JDK folder located in Program Files/Java. On MacOS, its located in Library/Java/JavaVirtualMachines.  
  
java_zip_archive_from_project: appends the folder your project files are located (the .sublime-project file folder) to the java_zip_archive_dir if you want to keep the zip with your project files. If, for example, you had a lib folder containing the src.zip in the same directory as the Sublime project file, this variable would be set to java_zip_archive_dir = '/lib/src.zip'.  
  
  
IMPORTANT CREDITS:  
ST2 Display-Functions: https://github.com/BoundInCode/Display-Functions  
ST2 JavaSetterGetter: https://github.com/enriquein/JavaSetterGetter
