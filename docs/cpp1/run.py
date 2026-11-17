import sys, lizard
info = lizard.FileAnalyzer(lizard.get_extensions([])).analyze_source_code("x.cpp", open(sys.argv[1]).read())
for f in info.function_list:
    print(f.name, f.start_line, f.end_line, "nloc", f.nloc, "ccn", f.cyclomatic_complexity, "params", f.parameter_count)
