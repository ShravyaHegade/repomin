package dev.repomin.internal;

import com.sun.source.tree.AnnotatedTypeTree;
import com.sun.source.tree.AnnotationTree;
import com.sun.source.tree.BinaryTree;
import com.sun.source.tree.BlockTree;
import com.sun.source.tree.ClassTree;
import com.sun.source.tree.CompilationUnitTree;
import com.sun.source.tree.ConditionalExpressionTree;
import com.sun.source.tree.ExpressionTree;
import com.sun.source.tree.ImportTree;
import com.sun.source.tree.IdentifierTree;
import com.sun.source.tree.LambdaExpressionTree;
import com.sun.source.tree.LiteralTree;
import com.sun.source.tree.MemberReferenceTree;
import com.sun.source.tree.MethodTree;
import com.sun.source.tree.MethodInvocationTree;
import com.sun.source.tree.ModifiersTree;
import com.sun.source.tree.NewArrayTree;
import com.sun.source.tree.NewClassTree;
import com.sun.source.tree.StatementTree;
import com.sun.source.tree.Tree;
import com.sun.source.tree.TypeCastTree;
import com.sun.source.tree.TypeParameterTree;
import com.sun.source.tree.UnaryTree;
import com.sun.source.tree.VariableTree;
import com.sun.source.util.JavacTask;
import com.sun.source.util.SourcePositions;
import com.sun.source.util.TreePath;
import com.sun.source.util.TreePathScanner;
import com.sun.source.util.Trees;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import javax.lang.model.element.Element;
import javax.lang.model.element.ElementKind;
import javax.lang.model.element.ExecutableElement;
import javax.lang.model.element.Modifier;
import javax.lang.model.element.NestingKind;
import javax.lang.model.element.TypeElement;
import javax.lang.model.element.TypeParameterElement;
import javax.lang.model.element.VariableElement;
import javax.lang.model.type.ArrayType;
import javax.lang.model.type.DeclaredType;
import javax.lang.model.type.ExecutableType;
import javax.lang.model.type.IntersectionType;
import javax.lang.model.type.TypeKind;
import javax.lang.model.type.TypeMirror;
import javax.lang.model.type.TypeVariable;
import javax.lang.model.type.UnionType;
import javax.lang.model.type.WildcardType;
import javax.lang.model.util.Elements;
import javax.lang.model.util.Types;
import javax.tools.Diagnostic;
import javax.tools.DiagnosticCollector;
import javax.tools.JavaCompiler;
import javax.tools.JavaFileObject;
import javax.tools.StandardJavaFileManager;
import javax.tools.StandardLocation;
import javax.tools.ToolProvider;

public final class JavaStructure {
    private static final PrintWriter OUTPUT = new PrintWriter(
            new OutputStreamWriter(System.out, StandardCharsets.UTF_8), true);
    private static final Map<String, int[]> UTF8_OFFSETS = new IdentityHashMap<>();

    private JavaStructure() {}

    public static void main(String[] args) throws Exception {
        JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) {
            System.err.println("A JDK with javac is required");
            System.exit(2);
        }

        DiagnosticCollector<JavaFileObject> diagnostics = new DiagnosticCollector<>();
        try (StandardJavaFileManager fileManager = compiler.getStandardFileManager(
                diagnostics, Locale.ROOT, StandardCharsets.UTF_8)) {
            AnalysisInputs inputs = analysisInputs(args);
            fileManager.setLocationFromPaths(
                    StandardLocation.CLASS_PATH, inputs.classpathPaths);
            // Only the explicit source list may contribute source declarations.
            fileManager.setLocationFromPaths(StandardLocation.SOURCE_PATH, List.of());
            Iterable<? extends JavaFileObject> files =
                    fileManager.getJavaFileObjectsFromStrings(inputs.sourcePaths);
            JavacTask task = (JavacTask) compiler.getTask(
                    null,
                    fileManager,
                    diagnostics,
                    Arrays.asList("-proc:none", "-encoding", "UTF-8"),
                    null,
                    files);
            List<CompilationUnitTree> units = new ArrayList<>();
            for (CompilationUnitTree unit : task.parse()) {
                units.add(unit);
            }
            Trees trees = Trees.instance(task);
            SourcePositions positions = trees.getSourcePositions();
            boolean attributionComplete = true;
            try {
                task.analyze();
            } catch (IOException | RuntimeException ignored) {
                // Broken reproductions can prevent complete attribution. Syntax
                // reductions remain useful, and unresolved symbols are never linked.
                attributionComplete = false;
            }
            if (attributionComplete && !hasOnlyRecoverableDiagnostics(diagnostics)) {
                attributionComplete = false;
            }
            if (attributionComplete
                    && (hasErrorTypedTypeHierarchy(units, trees)
                            || hasErrorTypedCalls(units, trees))) {
                attributionComplete = false;
            }

            List<ExecutableElement> sourceExecutables = attributionComplete
                    ? collectSourceExecutables(units, trees)
                    : List.of();
            Map<ExecutableElement, Integer> symbolIds = new HashMap<>();
            ExecutableSupport executableSupport = new ExecutableSupport(
                    trees, task.getElements(), task.getTypes(), sourceExecutables);
            for (CompilationUnitTree unit : units) {
                emitUnit(
                        unit,
                        positions,
                        trees,
                        executableSupport,
                        symbolIds,
                        attributionComplete);
            }
        }
    }

    private static List<ExecutableElement> collectSourceExecutables(
            List<CompilationUnitTree> units, Trees trees) {
        List<ExecutableElement> result = new ArrayList<>();
        for (CompilationUnitTree unit : units) {
            new TreePathScanner<Void, Void>() {
                @Override
                public Void visitMethod(MethodTree node, Void unused) {
                    ExecutableElement element = executableElement(trees, getCurrentPath());
                    if (element != null && !result.contains(element)) {
                        result.add(element);
                    }
                    return super.visitMethod(node, unused);
                }
            }.scan(unit, null);
        }
        return result;
    }

    private static AnalysisInputs analysisInputs(String[] args) throws IOException {
        if (args.length != 4
                || !"--source-list".equals(args[0])
                || !"--classpath-list".equals(args[2])) {
            throw new IllegalArgumentException(
                    "expected --source-list FILE --classpath-list FILE");
        }
        List<Path> classpathPaths = new ArrayList<>();
        for (String path : nulDelimitedPaths(Path.of(args[3]))) {
            classpathPaths.add(Path.of(path));
        }
        return new AnalysisInputs(nulDelimitedPaths(Path.of(args[1])), classpathPaths);
    }

    private static List<String> nulDelimitedPaths(Path listPath) throws IOException {
        String contents = Files.readString(listPath, StandardCharsets.UTF_8);
        List<String> paths = new ArrayList<>();
        int start = 0;
        for (int index = 0; index <= contents.length(); index++) {
            if (index == contents.length() || contents.charAt(index) == 0) {
                if (index > start) {
                    paths.add(contents.substring(start, index));
                }
                start = index + 1;
            }
        }
        return paths;
    }

    private static final class AnalysisInputs {
        final List<String> sourcePaths;
        final List<Path> classpathPaths;

        AnalysisInputs(List<String> sourcePaths, List<Path> classpathPaths) {
            this.sourcePaths = sourcePaths;
            this.classpathPaths = classpathPaths;
        }
    }

    private static void emitUnit(
            CompilationUnitTree unit,
            SourcePositions positions,
            Trees trees,
            ExecutableSupport executableSupport,
            Map<ExecutableElement, Integer> symbolIds,
            boolean attributionComplete)
            throws IOException {
        Path path = Path.of(unit.getSourceFile().toUri()).toAbsolutePath().normalize();
        String source = Files.readString(path, StandardCharsets.UTF_8);

        for (AnnotationTree annotation : unit.getPackageAnnotations()) {
            emit(
                    path,
                    source,
                    unit,
                    annotation,
                    "annotation",
                    annotation.getAnnotationType().toString(),
                    positions,
                    "");
        }

        for (ImportTree importTree : unit.getImports()) {
            emit(
                    path,
                    source,
                    unit,
                    importTree,
                    "import",
                    importTree.getQualifiedIdentifier().toString(),
                    positions);
        }

        new TreePathScanner<Void, Void>() {
            @Override
            public Void visitClass(ClassTree node, Void unused) {
                emitAnnotations(path, source, unit, node.getModifiers(), positions);
                for (Tree member : node.getMembers()) {
                    emit(
                            path,
                            source,
                            unit,
                            member,
                            "member",
                            memberLabel(member),
                            positions);
                }
                return super.visitClass(node, unused);
            }

            @Override
            public Void visitMethod(MethodTree node, Void unused) {
                emitAnnotations(path, source, unit, node.getModifiers(), positions);
                emitListRemovals(
                        path,
                        source,
                        unit,
                        node.getParameters(),
                        "parameter",
                        "method:" + node.getName(),
                        positions);
                ExecutableElement element = attributionComplete
                        ? executableElement(trees, getCurrentPath())
                        : null;
                if (element != null) {
                    emitLinkedParameterDeclarations(
                            path,
                            source,
                            unit,
                            node,
                    element,
                    executableSupport,
                            positions,
                            symbolIds);
                }
                return super.visitMethod(node, unused);
            }

            @Override
            public Void visitVariable(VariableTree node, Void unused) {
                emitAnnotations(path, source, unit, node.getModifiers(), positions);
                return super.visitVariable(node, unused);
            }

            @Override
            public Void visitAnnotatedType(AnnotatedTypeTree node, Void unused) {
                for (AnnotationTree annotation : node.getAnnotations()) {
                    emit(
                            path,
                            source,
                            unit,
                            annotation,
                            "annotation",
                            annotation.getAnnotationType().toString(),
                            positions,
                            "");
                }
                return super.visitAnnotatedType(node, unused);
            }

            @Override
            public Void visitTypeParameter(TypeParameterTree node, Void unused) {
                for (AnnotationTree annotation : node.getAnnotations()) {
                    emit(
                            path,
                            source,
                            unit,
                            annotation,
                            "annotation",
                            annotation.getAnnotationType().toString(),
                            positions,
                            "");
                }
                return super.visitTypeParameter(node, unused);
            }

            @Override
            public Void visitBlock(BlockTree node, Void unused) {
                for (StatementTree statement : node.getStatements()) {
                    emit(
                            path,
                            source,
                            unit,
                            statement,
                            "statement",
                            statement.getKind().name().toLowerCase(Locale.ROOT),
                            positions);
                }
                return super.visitBlock(node, unused);
            }

            @Override
            public Void visitLambdaExpression(LambdaExpressionTree node, Void unused) {
                emitListRemovals(
                        path,
                        source,
                        unit,
                        node.getParameters(),
                        "parameter",
                        "lambda",
                        positions);
                return super.visitLambdaExpression(node, unused);
            }

            @Override
            public Void visitMethodInvocation(MethodInvocationTree node, Void unused) {
                emitListRemovals(
                        path,
                        source,
                        unit,
                        node.getArguments(),
                        "argument",
                        node.getMethodSelect().toString(),
                        positions);
                ExecutableElement element = attributionComplete
                        ? executableElement(trees, getCurrentPath())
                        : null;
                if (element != null) {
                    emitLinkedArguments(
                            path,
                            source,
                            unit,
                            node.getArguments(),
                    element,
                    executableSupport,
                            positions,
                            symbolIds);
                }
                return super.visitMethodInvocation(node, unused);
            }

            @Override
            public Void visitNewClass(NewClassTree node, Void unused) {
                emitListRemovals(
                        path,
                        source,
                        unit,
                        node.getArguments(),
                        "argument",
                        "new:" + node.getIdentifier(),
                        positions);
                ExecutableElement element = attributionComplete
                        ? executableElement(trees, getCurrentPath())
                        : null;
                if (element != null && node.getClassBody() != null) {
                    TypeElement baseType = newClassType(trees, getCurrentPath(), node);
                    if (baseType != null) {
                        emitConstructorBlockers(
                                path,
                                source,
                                unit,
                                node,
                                baseType,
                                executableSupport,
                                positions,
                                symbolIds);
                    }
                } else if (element != null) {
                    emitLinkedArguments(
                            path,
                            source,
                            unit,
                            node.getArguments(),
                            element,
                            executableSupport,
                            positions,
                            symbolIds);
                }
                return super.visitNewClass(node, unused);
            }

            @Override
            public Void visitIdentifier(IdentifierTree node, Void unused) {
                if (attributionComplete) {
                    Element element = element(trees, getCurrentPath());
                    if (element instanceof VariableElement
                            && element.getEnclosingElement() instanceof ExecutableElement) {
                        ExecutableElement executable =
                                (ExecutableElement) element.getEnclosingElement();
                        int index = executable.getParameters().indexOf(element);
                        if (index >= 0) {
                            emitParameterBlocker(
                                    path,
                                    source,
                                    unit,
                                    node,
                            executable,
                            index,
                            executableSupport,
                                    positions,
                                    symbolIds);
                        }
                    }
                }
                return super.visitIdentifier(node, unused);
            }

            @Override
            public Void visitMemberReference(MemberReferenceTree node, Void unused) {
                ExecutableElement element = attributionComplete
                        ? executableElement(trees, getCurrentPath())
                        : null;
                if (element != null && executableSupport.supports(element)) {
                    for (int index = 0; index < element.getParameters().size(); index++) {
                        emitParameterBlocker(
                                path,
                                source,
                                unit,
                                node,
                                element,
                                index,
                                executableSupport,
                                positions,
                                symbolIds);
                    }
                }
                return super.visitMemberReference(node, unused);
            }

            @Override
            public Void visitNewArray(NewArrayTree node, Void unused) {
                List<? extends ExpressionTree> initializers = node.getInitializers();
                if (initializers != null) {
                    emitListRemovals(
                            path,
                            source,
                            unit,
                            initializers,
                            "argument",
                            "array-initializer",
                            positions);
                }
                return super.visitNewArray(node, unused);
            }

            @Override
            public Void visitBinary(BinaryTree node, Void unused) {
                emitTreeReplacement(
                        path,
                        source,
                        unit,
                        node,
                        node.getLeftOperand(),
                        node.getKind().name().toLowerCase(Locale.ROOT) + ":left",
                        positions);
                emitTreeReplacement(
                        path,
                        source,
                        unit,
                        node,
                        node.getRightOperand(),
                        node.getKind().name().toLowerCase(Locale.ROOT) + ":right",
                        positions);
                return super.visitBinary(node, unused);
            }

            @Override
            public Void visitConditionalExpression(
                    ConditionalExpressionTree node, Void unused) {
                emitTreeReplacement(
                        path,
                        source,
                        unit,
                        node,
                        node.getTrueExpression(),
                        "conditional:true",
                        positions);
                emitTreeReplacement(
                        path,
                        source,
                        unit,
                        node,
                        node.getFalseExpression(),
                        "conditional:false",
                        positions);
                return super.visitConditionalExpression(node, unused);
            }

            @Override
            public Void visitTypeCast(TypeCastTree node, Void unused) {
                emitTreeReplacement(
                        path,
                        source,
                        unit,
                        node,
                        node.getExpression(),
                        "cast:operand",
                        positions);
                return super.visitTypeCast(node, unused);
            }

            @Override
            public Void visitUnary(UnaryTree node, Void unused) {
                emitTreeReplacement(
                        path,
                        source,
                        unit,
                        node,
                        node.getExpression(),
                        node.getKind().name().toLowerCase(Locale.ROOT) + ":operand",
                        positions);
                return super.visitUnary(node, unused);
            }

            @Override
            public Void visitLiteral(LiteralTree node, Void unused) {
                String replacement = literalReplacement(node);
                if (replacement != null
                        && !replacement.equals(sourceOf(source, unit, node, positions))) {
                    emit(
                            path,
                            source,
                            unit,
                            node,
                            "literal",
                            node.getKind().name().toLowerCase(Locale.ROOT),
                            positions,
                            replacement);
                }
                return super.visitLiteral(node, unused);
            }
        }.scan(unit, null);
    }

    private static boolean hasOnlyRecoverableDiagnostics(
            DiagnosticCollector<JavaFileObject> diagnostics) {
        for (Diagnostic<? extends JavaFileObject> diagnostic : diagnostics.getDiagnostics()) {
            if (diagnostic.getKind() != Diagnostic.Kind.ERROR) {
                continue;
            }
            String code = diagnostic.getCode();
            if ("compiler.err.doesnt.exist".equals(code)
                    || code.startsWith("compiler.err.cant.resolve")) {
                continue;
            }
            return false;
        }
        return true;
    }

    private static boolean hasErrorTypedTypeHierarchy(
            List<CompilationUnitTree> units, Trees trees) {
        boolean[] unsafe = {false};
        for (CompilationUnitTree unit : units) {
            new TreePathScanner<Void, Void>() {
                @Override
                public Void visitClass(ClassTree node, Void unused) {
                    Element candidate = element(trees, getCurrentPath());
                    if (candidate instanceof TypeElement
                            && typeElementHasErrorType((TypeElement) candidate)) {
                        unsafe[0] = true;
                        return null;
                    }
                    return super.visitClass(node, unused);
                }
            }.scan(unit, null);
            if (unsafe[0]) {
                return true;
            }
        }
        return false;
    }

    private static boolean typeElementHasErrorType(TypeElement type) {
        try {
            if (containsErrorType(type.asType())
                    || containsErrorType(type.getSuperclass())) {
                return true;
            }
            for (TypeMirror interfaceType : type.getInterfaces()) {
                if (containsErrorType(interfaceType)) {
                    return true;
                }
            }
            for (TypeParameterElement parameter : type.getTypeParameters()) {
                for (TypeMirror bound : parameter.getBounds()) {
                    if (containsErrorType(bound)) {
                        return true;
                    }
                }
            }
            return false;
        } catch (RuntimeException ignored) {
            return true;
        }
    }

    private static boolean hasErrorTypedCalls(
            List<CompilationUnitTree> units, Trees trees) {
        boolean[] unsafe = {false};
        int[] callDepth = {0};
        for (CompilationUnitTree unit : units) {
            new TreePathScanner<Void, Void>() {
                @Override
                public Void scan(Tree tree, Void unused) {
                    if (tree == null || unsafe[0]) {
                        return null;
                    }
                    if (callDepth[0] > 0) {
                        TreePath parent = getCurrentPath();
                        if (parent != null
                                && pathContainsErrorType(
                                        trees, new TreePath(parent, tree))) {
                            unsafe[0] = true;
                            return null;
                        }
                    }
                    return super.scan(tree, unused);
                }

                @Override
                public Void visitMethodInvocation(MethodInvocationTree node, Void unused) {
                    if (pathHasErrorType(trees, getCurrentPath())
                            || executableHasErrorType(trees, getCurrentPath())) {
                        unsafe[0] = true;
                        return null;
                    }
                    callDepth[0]++;
                    try {
                        return super.visitMethodInvocation(node, unused);
                    } finally {
                        callDepth[0]--;
                    }
                }

                @Override
                public Void visitNewClass(NewClassTree node, Void unused) {
                    if (pathHasErrorType(trees, getCurrentPath())
                            || executableHasErrorType(trees, getCurrentPath())) {
                        unsafe[0] = true;
                        return null;
                    }
                    callDepth[0]++;
                    try {
                        return super.visitNewClass(node, unused);
                    } finally {
                        callDepth[0]--;
                    }
                }

                @Override
                public Void visitMemberReference(MemberReferenceTree node, Void unused) {
                    if (pathHasErrorType(trees, getCurrentPath())
                            || executableHasErrorType(trees, getCurrentPath())) {
                        unsafe[0] = true;
                        return null;
                    }
                    callDepth[0]++;
                    try {
                        return super.visitMemberReference(node, unused);
                    } finally {
                        callDepth[0]--;
                    }
                }
            }.scan(unit, null);
            if (unsafe[0]) {
                return true;
            }
        }
        return false;
    }

    private static boolean pathHasErrorType(Trees trees, TreePath path) {
        try {
            TypeMirror type = trees.getTypeMirror(path);
            return type == null || containsErrorType(type);
        } catch (RuntimeException ignored) {
            return true;
        }
    }

    private static boolean pathContainsErrorType(Trees trees, TreePath path) {
        try {
            return containsErrorType(trees.getTypeMirror(path));
        } catch (RuntimeException ignored) {
            return true;
        }
    }

    private static boolean executableHasErrorType(Trees trees, TreePath path) {
        Element candidate = element(trees, path);
        if (!(candidate instanceof ExecutableElement)) {
            return false;
        }
        try {
            return containsErrorType(candidate.asType());
        } catch (RuntimeException ignored) {
            return true;
        }
    }

    private static boolean containsErrorType(TypeMirror type) {
        return containsErrorType(type, new IdentityHashMap<TypeMirror, Boolean>());
    }

    private static boolean containsErrorType(
            TypeMirror type, IdentityHashMap<TypeMirror, Boolean> visited) {
        if (type == null) {
            return false;
        }
        if (type.getKind() == TypeKind.ERROR) {
            return true;
        }
        if (visited.put(type, Boolean.TRUE) != null) {
            return false;
        }
        switch (type.getKind()) {
            case ARRAY:
                return containsErrorType(
                        ((ArrayType) type).getComponentType(), visited);
            case DECLARED:
                DeclaredType declared = (DeclaredType) type;
                if (containsErrorType(declared.getEnclosingType(), visited)) {
                    return true;
                }
                for (TypeMirror argument : declared.getTypeArguments()) {
                    if (containsErrorType(argument, visited)) {
                        return true;
                    }
                }
                return false;
            case EXECUTABLE:
                ExecutableType executable = (ExecutableType) type;
                for (TypeVariable variable : executable.getTypeVariables()) {
                    if (containsErrorType(variable, visited)) {
                        return true;
                    }
                }
                if (containsErrorType(executable.getReceiverType(), visited)) {
                    return true;
                }
                if (containsErrorType(executable.getReturnType(), visited)) {
                    return true;
                }
                for (TypeMirror parameter : executable.getParameterTypes()) {
                    if (containsErrorType(parameter, visited)) {
                        return true;
                    }
                }
                for (TypeMirror thrown : executable.getThrownTypes()) {
                    if (containsErrorType(thrown, visited)) {
                        return true;
                    }
                }
                return false;
            case TYPEVAR:
                TypeVariable variable = (TypeVariable) type;
                return containsErrorType(variable.getUpperBound(), visited)
                        || containsErrorType(variable.getLowerBound(), visited);
            case INTERSECTION:
                for (TypeMirror bound : ((IntersectionType) type).getBounds()) {
                    if (containsErrorType(bound, visited)) {
                        return true;
                    }
                }
                return false;
            case UNION:
                for (TypeMirror alternative : ((UnionType) type).getAlternatives()) {
                    if (containsErrorType(alternative, visited)) {
                        return true;
                    }
                }
                return false;
            case WILDCARD:
                WildcardType wildcard = (WildcardType) type;
                return containsErrorType(wildcard.getExtendsBound(), visited)
                        || containsErrorType(wildcard.getSuperBound(), visited);
            default:
                return false;
        }
    }

    private static void emitAnnotations(
            Path path,
            String source,
            CompilationUnitTree unit,
            ModifiersTree modifiers,
            SourcePositions positions) {
        for (AnnotationTree annotation : modifiers.getAnnotations()) {
            emit(
                    path,
                    source,
                    unit,
                    annotation,
                    "annotation",
                    annotation.getAnnotationType().toString(),
                    positions,
                    "");
        }
    }

    private static Element element(Trees trees, TreePath treePath) {
        try {
            return trees.getElement(treePath);
        } catch (RuntimeException ignored) {
            // A partially attributed tree has no safe symbol identity.
        }
        return null;
    }

    private static ExecutableElement executableElement(Trees trees, TreePath treePath) {
        Element element = element(trees, treePath);
        return element instanceof ExecutableElement ? (ExecutableElement) element : null;
    }

    private static TypeElement newClassType(
            Trees trees, TreePath treePath, NewClassTree newClass) {
        Element element = element(trees, new TreePath(treePath, newClass.getIdentifier()));
        return element instanceof TypeElement ? (TypeElement) element : null;
    }

    private static boolean isSourceExecutable(Trees trees, ExecutableElement element) {
        try {
            return trees.getPath(element) != null;
        } catch (RuntimeException ignored) {
            return false;
        }
    }

    private static final class ExecutableSupport {
        private final Trees trees;
        private final Elements elements;
        private final Types types;
        private final List<ExecutableElement> sourceExecutables;
        private final Map<ExecutableElement, Boolean> supported = new HashMap<>();
        private final Map<ExecutableElement, ExecutableElement> representatives =
                new HashMap<>();
        private final Map<TypeElement, List<DeclaredType>> superTypes = new HashMap<>();

        ExecutableSupport(
                Trees trees,
                Elements elements,
                Types types,
                List<ExecutableElement> sourceExecutables) {
            this.trees = trees;
            this.elements = elements;
            this.types = types;
            this.sourceExecutables = sourceExecutables;
        }

        boolean supports(ExecutableElement element) {
            Boolean cached = supported.get(element);
            if (cached != null) {
                return cached;
            }
            boolean result;
            try {
                result = computeSupport(element);
            } catch (RuntimeException ignored) {
                result = false;
            }
            supported.put(element, result);
            return result;
        }

        private boolean computeSupport(ExecutableElement element) {
            if (!isSourceExecutable(trees, element)
                    || element.getModifiers().contains(Modifier.NATIVE)) {
                return false;
            }
            if (element.getKind() == ElementKind.CONSTRUCTOR) {
                return !"RECORD".equals(element.getEnclosingElement().getKind().name());
            }
            if (element.getKind() != ElementKind.METHOD) {
                return false;
            }
            if (element.getModifiers().contains(Modifier.PRIVATE)
                    || element.getModifiers().contains(Modifier.STATIC)) {
                return true;
            }
            if (element.getModifiers().contains(Modifier.PUBLIC)
                    || element.getModifiers().contains(Modifier.PROTECTED)) {
                return false;
            }
            if (!(element.getEnclosingElement() instanceof TypeElement)) {
                return false;
            }
            TypeElement owner = (TypeElement) element.getEnclosingElement();
            NestingKind nesting = owner.getNestingKind();
            if (owner.getKind() != ElementKind.CLASS
                    || (nesting != NestingKind.TOP_LEVEL && nesting != NestingKind.MEMBER)
                    || element.getModifiers().contains(Modifier.ABSTRACT)
                    || containsErrorType(element.asType())
                    || containsErrorType(owner.asType())) {
                return false;
            }
            if (!overrideFamilySupported(element, owner)) {
                return false;
            }
            return !hasProspectiveSignatureCollision(element, owner);
        }

        ExecutableElement groupRepresentative(ExecutableElement element) {
            ExecutableElement cached = representatives.get(element);
            if (cached != null) {
                return cached;
            }
            List<ExecutableElement> family = overrideFamily(element);
            ExecutableElement representative = element;
            if (family.size() > 1) {
                List<ExecutableElement> roots = new ArrayList<>();
                for (ExecutableElement candidate : family) {
                    TypeElement candidateOwner = owner(candidate);
                    boolean overridesFamilyMember = false;
                    for (ExecutableElement other : family) {
                        if (candidate.equals(other)) {
                            continue;
                        }
                        TypeElement otherOwner = owner(other);
                        if (candidateOwner != null
                                && otherOwner != null
                                && elements.overrides(candidate, other, candidateOwner)) {
                            overridesFamilyMember = true;
                            break;
                        }
                    }
                    if (!overridesFamilyMember) {
                        roots.add(candidate);
                    }
                }
                if (roots.size() == 1) {
                    representative = roots.get(0);
                }
            }
            representatives.put(element, representative);
            return representative;
        }

        private boolean overrideFamilySupported(
                ExecutableElement element, TypeElement owner) {
            // A source method that overrides an external declaration has an
            // open dispatch contract; removing an argument would change that
            // binary/source boundary even when the external member is not in
            // the source executable list.
            if (hasExternalOverride(element, owner)) {
                return false;
            }
            List<ExecutableElement> family = overrideFamily(element);
            if (family.size() <= 1) {
                return element.getModifiers().contains(Modifier.FINAL)
                        || owner.getModifiers().contains(Modifier.FINAL);
            }
            int parameterCount = element.getParameters().size();
            for (ExecutableElement member : family) {
                TypeElement memberOwner = owner(member);
                if (memberOwner == null
                        || member.getParameters().size() != parameterCount
                        || !isOrdinarySourceOwner(memberOwner)
                        || member.getModifiers().contains(Modifier.PRIVATE)
                        || member.getModifiers().contains(Modifier.STATIC)
                        || member.getModifiers().contains(Modifier.ABSTRACT)
                        || member.getModifiers().contains(Modifier.PUBLIC)
                        || member.getModifiers().contains(Modifier.PROTECTED)
                        || !memberOwner.getTypeParameters().isEmpty()
                        || !member.getTypeParameters().isEmpty()
                        || containsErrorType(member.asType())) {
                    return false;
                }
                if (hasExternalOverride(member, memberOwner)) {
                    return false;
                }
            }
            // A source-local family must have one root. Multiple roots indicate
            // interface/diamond dispatch that cannot be represented by one edit group.
            if (groupRootCount(family) != 1) {
                return false;
            }
            // The source family is safe only when its terminal implementation is
            // final. This closes dispatch for subclasses outside the source set.
            for (ExecutableElement candidate : family) {
                TypeElement candidateOwner = owner(candidate);
                boolean hasSourceChild = false;
                for (ExecutableElement other : family) {
                    if (candidate.equals(other)) {
                        continue;
                    }
                    TypeElement otherOwner = owner(other);
                    if (candidateOwner != null
                            && otherOwner != null
                            && elements.overrides(other, candidate, otherOwner)) {
                        hasSourceChild = true;
                        break;
                    }
                }
                if (!hasSourceChild
                        && (candidate.getModifiers().contains(Modifier.FINAL)
                                || (candidateOwner != null
                                        && candidateOwner.getModifiers().contains(
                                                Modifier.FINAL)))) {
                    return true;
                }
            }
            return false;
        }

        private boolean hasExternalOverride(
                ExecutableElement method, TypeElement owner) {
            for (DeclaredType declaredSuper : completeSuperTypes(owner)) {
                Element candidateOwner = types.asElement(declaredSuper);
                if (!(candidateOwner instanceof TypeElement)) {
                    return true;
                }
                TypeElement superOwner = (TypeElement) candidateOwner;
                for (Element member : superOwner.getEnclosedElements()) {
                    if (member.getKind() == ElementKind.METHOD
                            && member.getSimpleName().contentEquals(method.getSimpleName())
                            && elements.overrides(
                                    method, (ExecutableElement) member, owner)) {
                        ExecutableElement superMethod = (ExecutableElement) member;
                        if (!sourceExecutables.contains(superMethod)
                                || superOwner.getKind() == ElementKind.INTERFACE) {
                            return true;
                        }
                    }
                }
            }
            return false;
        }

        private int groupRootCount(List<ExecutableElement> family) {
            int roots = 0;
            for (ExecutableElement candidate : family) {
                TypeElement candidateOwner = owner(candidate);
                boolean hasSourceSuper = false;
                for (ExecutableElement other : family) {
                    if (candidate.equals(other)) {
                        continue;
                    }
                    TypeElement otherOwner = owner(other);
                    if (candidateOwner != null
                            && otherOwner != null
                            && elements.overrides(candidate, other, candidateOwner)) {
                        hasSourceSuper = true;
                        break;
                    }
                }
                if (!hasSourceSuper) {
                    roots++;
                }
            }
            return roots;
        }

        private List<ExecutableElement> overrideFamily(ExecutableElement element) {
            List<ExecutableElement> result = new ArrayList<>();
            result.add(element);
            boolean changed;
            do {
                changed = false;
                for (ExecutableElement candidate : sourceExecutables) {
                    if (result.contains(candidate)
                            || !candidate.getSimpleName().contentEquals(
                                    element.getSimpleName())
                            || candidate.getParameters().size()
                                    != element.getParameters().size()) {
                        continue;
                    }
                    TypeElement candidateOwner = owner(candidate);
                    if (candidateOwner == null) {
                        continue;
                    }
                    for (ExecutableElement member : new ArrayList<>(result)) {
                        TypeElement memberOwner = owner(member);
                        if (memberOwner != null
                                && (elements.overrides(candidate, member, candidateOwner)
                                        || elements.overrides(member, candidate, memberOwner))) {
                            result.add(candidate);
                            changed = true;
                            break;
                        }
                    }
                }
            } while (changed);
            return result;
        }

        private TypeElement owner(ExecutableElement element) {
            return element.getEnclosingElement() instanceof TypeElement
                    ? (TypeElement) element.getEnclosingElement()
                    : null;
        }

        private boolean isOrdinarySourceOwner(TypeElement owner) {
            NestingKind nesting = owner.getNestingKind();
            return owner.getKind() == ElementKind.CLASS
                    && (nesting == NestingKind.TOP_LEVEL || nesting == NestingKind.MEMBER)
                    && isSourceExecutableOwner(owner);
        }

        private boolean isSourceExecutableOwner(TypeElement owner) {
            try {
                return trees.getPath(owner) != null;
            } catch (RuntimeException ignored) {
                return false;
            }
        }

        private boolean overridesSuperMethod(
                ExecutableElement method, TypeElement owner) {
            for (DeclaredType declaredSuper : completeSuperTypes(owner)) {
                Element candidateOwner = types.asElement(declaredSuper);
                if (!(candidateOwner instanceof TypeElement)) {
                    return true;
                }
                TypeElement superOwner = (TypeElement) candidateOwner;
                for (Element member : superOwner.getEnclosedElements()) {
                    if (member.getKind() != ElementKind.METHOD
                            || !member.getSimpleName().contentEquals(
                                    method.getSimpleName())) {
                        continue;
                    }
                    ExecutableElement superMethod = (ExecutableElement) member;
                    if (containsErrorType(superMethod.asType())
                            || elements.overrides(method, superMethod, owner)) {
                        return true;
                    }
                }
            }
            return false;
        }

        private List<DeclaredType> completeSuperTypes(TypeElement owner) {
            List<DeclaredType> cached = superTypes.get(owner);
            if (cached != null) {
                return cached;
            }
            List<TypeMirror> pending = new ArrayList<>(
                    types.directSupertypes(owner.asType()));
            List<DeclaredType> result = new ArrayList<>();
            while (!pending.isEmpty()) {
                TypeMirror candidate = pending.remove(pending.size() - 1);
                if (containsErrorType(candidate)
                        || candidate.getKind() != TypeKind.DECLARED) {
                    throw new IllegalStateException("incomplete executable hierarchy");
                }
                DeclaredType declared = (DeclaredType) candidate;
                boolean alreadySeen = false;
                for (DeclaredType seen : result) {
                    if (types.isSameType(seen, declared)) {
                        alreadySeen = true;
                        break;
                    }
                }
                if (alreadySeen) {
                    continue;
                }
                Element declaredElement = types.asElement(declared);
                if (!(declaredElement instanceof TypeElement)
                        || containsErrorType(declaredElement.asType())) {
                    throw new IllegalStateException("invalid executable hierarchy");
                }
                result.add(declared);
                pending.addAll(types.directSupertypes(declared));
            }
            superTypes.put(owner, result);
            return result;
        }

        private boolean hasProspectiveSignatureCollision(
                ExecutableElement method, TypeElement owner) {
            if (method.getParameters().isEmpty()
                    || owner.asType().getKind() != TypeKind.DECLARED) {
                return false;
            }
            DeclaredType ownerType = (DeclaredType) owner.asType();
            TypeMirror methodMember = types.asMemberOf(ownerType, method);
            TypeMirror methodDeclaration = method.asType();
            if (methodMember.getKind() != TypeKind.EXECUTABLE
                    || methodDeclaration.getKind() != TypeKind.EXECUTABLE
                    || containsErrorType(methodMember)
                    || containsErrorType(methodDeclaration)) {
                return true;
            }
            List<? extends TypeMirror> memberParameters =
                    ((ExecutableType) methodMember).getParameterTypes();
            List<? extends TypeMirror> declarationParameters =
                    ((ExecutableType) methodDeclaration).getParameterTypes();
            int prospectiveCount = memberParameters.size() - 1;
            if (hasProspectiveCollisionWithMembers(
                    method,
                    ownerType,
                    owner.getEnclosedElements(),
                    memberParameters,
                    declarationParameters,
                    prospectiveCount,
                    false)) {
                return true;
            }
            for (DeclaredType superType : completeSuperTypes(owner)) {
                Element superElement = types.asElement(superType);
                if (!(superElement instanceof TypeElement)
                        || hasProspectiveCollisionWithMembers(
                                method,
                                superType,
                                ((TypeElement) superElement).getEnclosedElements(),
                                memberParameters,
                                declarationParameters,
                                prospectiveCount,
                                true)) {
                    return true;
                }
            }
            return false;
        }

        private boolean hasProspectiveCollisionWithMembers(
                ExecutableElement method,
                DeclaredType declaringView,
                List<? extends Element> members,
                List<? extends TypeMirror> memberParameters,
                List<? extends TypeMirror> declarationParameters,
                int prospectiveCount,
                boolean inherited) {
            for (Element member : members) {
                if (member.getKind() != ElementKind.METHOD
                        || (inherited
                                && member.getModifiers().contains(Modifier.PRIVATE))
                        || !member.getSimpleName().contentEquals(method.getSimpleName())) {
                    continue;
                }
                ExecutableElement candidate = (ExecutableElement) member;
                if (candidate.equals(method)
                        || candidate.getParameters().size() != prospectiveCount) {
                    continue;
                }
                TypeMirror candidateMember = types.asMemberOf(declaringView, candidate);
                TypeMirror candidateDeclaration = candidate.asType();
                if (candidateMember.getKind() != TypeKind.EXECUTABLE
                        || candidateDeclaration.getKind() != TypeKind.EXECUTABLE
                        || containsErrorType(candidateMember)
                        || containsErrorType(candidateDeclaration)) {
                    return true;
                }
                List<? extends TypeMirror> candidateMemberParameters =
                        ((ExecutableType) candidateMember).getParameterTypes();
                List<? extends TypeMirror> candidateDeclarationParameters =
                        ((ExecutableType) candidateDeclaration).getParameterTypes();
                for (int removed = 0; removed < memberParameters.size(); removed++) {
                    if (sameErasedParametersAfterRemoval(
                                    memberParameters,
                                    candidateMemberParameters,
                                    removed)
                            || sameErasedParametersAfterRemoval(
                                    declarationParameters,
                                    candidateDeclarationParameters,
                                    removed)
                            || sameErasedParametersAfterRemoval(
                                    memberParameters,
                                    candidateDeclarationParameters,
                                    removed)
                            || sameErasedParametersAfterRemoval(
                                    declarationParameters,
                                    candidateMemberParameters,
                                    removed)) {
                        return true;
                    }
                }
            }
            return false;
        }

        private boolean sameErasedParametersAfterRemoval(
                List<? extends TypeMirror> parameters,
                List<? extends TypeMirror> candidateParameters,
                int removed) {
            int candidateIndex = 0;
            for (int index = 0; index < parameters.size(); index++) {
                if (index == removed) {
                    continue;
                }
                TypeMirror left = types.erasure(parameters.get(index));
                TypeMirror right = types.erasure(candidateParameters.get(candidateIndex));
                if (!types.isSameType(left, right)) {
                    return false;
                }
                candidateIndex++;
            }
            return true;
        }
    }

    private static void emitLinkedParameterDeclarations(
            Path path,
            String source,
            CompilationUnitTree unit,
            MethodTree method,
            ExecutableElement element,
            ExecutableSupport executableSupport,
            SourcePositions positions,
            Map<ExecutableElement, Integer> symbolIds) {
        if (!executableSupport.supports(element)) {
            return;
        }
        List<? extends VariableTree> parameters = method.getParameters();
        int count = Math.min(parameters.size(), element.getParameters().size());
        for (int index = 0; index < count; index++) {
            emitListRemoval(
                    path,
                    source,
                    unit,
                    parameters,
                    index,
                    index + 1,
                    "coordinated-parameter",
                    symbolLabel(element, index),
                    symbolGroup(element, index, executableSupport, symbolIds),
                    "declaration",
                    positions);
        }
    }

    private static void emitLinkedArguments(
            Path path,
            String source,
            CompilationUnitTree unit,
            List<? extends ExpressionTree> arguments,
            ExecutableElement element,
            ExecutableSupport executableSupport,
            SourcePositions positions,
            Map<ExecutableElement, Integer> symbolIds) {
        if (!executableSupport.supports(element)) {
            return;
        }
        int parameterCount = element.getParameters().size();
        for (int index = 0; index < parameterCount; index++) {
            if (index >= arguments.size()) {
                continue;
            }
            int end = index + 1;
            if (element.isVarArgs() && index == parameterCount - 1) {
                end = arguments.size();
            }
            emitListRemoval(
                    path,
                    source,
                    unit,
                    arguments,
                    index,
                    end,
                    "coordinated-parameter",
                    symbolLabel(element, index),
                    symbolGroup(element, index, executableSupport, symbolIds),
                    "call",
                    positions);
        }
    }

    private static void emitParameterBlocker(
            Path path,
            String source,
            CompilationUnitTree unit,
            Tree blocker,
            ExecutableElement element,
            int parameterIndex,
            ExecutableSupport executableSupport,
            SourcePositions positions,
            Map<ExecutableElement, Integer> symbolIds) {
        if (!executableSupport.supports(element)) {
            return;
        }
        long start = positions.getStartPosition(unit, blocker);
        long end = positions.getEndPosition(unit, blocker);
        emitRange(
                path,
                source,
                start,
                end,
                "coordinated-parameter",
                symbolLabel(element, parameterIndex),
                "",
                symbolGroup(element, parameterIndex, executableSupport, symbolIds),
                "blocker");
    }

    private static void emitConstructorBlockers(
            Path path,
            String source,
            CompilationUnitTree unit,
            NewClassTree newClass,
            TypeElement baseType,
            ExecutableSupport executableSupport,
            SourcePositions positions,
            Map<ExecutableElement, Integer> symbolIds) {
        for (Element member : baseType.getEnclosedElements()) {
            if (!(member instanceof ExecutableElement)
                    || member.getKind() != ElementKind.CONSTRUCTOR) {
                continue;
            }
            ExecutableElement constructor = (ExecutableElement) member;
            if (!executableSupport.supports(constructor)) {
                continue;
            }
            for (int index = 0; index < constructor.getParameters().size(); index++) {
                emitParameterBlocker(
                        path,
                        source,
                        unit,
                        newClass,
                        constructor,
                        index,
                        executableSupport,
                        positions,
                        symbolIds);
            }
        }
    }

    private static String symbolGroup(
            ExecutableElement element,
            int parameterIndex,
            ExecutableSupport executableSupport,
            Map<ExecutableElement, Integer> symbolIds) {
        ExecutableElement representative = executableSupport.groupRepresentative(element);
        Integer symbolId = symbolIds.get(representative);
        if (symbolId == null) {
            symbolId = symbolIds.size() + 1;
            symbolIds.put(representative, symbolId);
        }
        return "executable:" + symbolId + ":parameter:" + parameterIndex;
    }

    private static String symbolLabel(ExecutableElement element, int parameterIndex) {
        return element.getEnclosingElement()
                + "#"
                + element
                + ":"
                + parameterIndex;
    }

    private static void emitListRemovals(
            Path path,
            String source,
            CompilationUnitTree unit,
            List<? extends Tree> items,
            String kind,
            String owner,
            SourcePositions positions) {
        for (int index = 0; index < items.size(); index++) {
            emitListRemoval(
                    path,
                    source,
                    unit,
                    items,
                    index,
                    index + 1,
                    kind,
                    owner + ":" + index,
                    null,
                    null,
                    positions);
        }
    }

    private static void emitListRemoval(
            Path path,
            String source,
            CompilationUnitTree unit,
            List<? extends Tree> items,
            int fromIndex,
            int toIndex,
            String kind,
            String label,
            String group,
            String role,
            SourcePositions positions) {
        if (fromIndex < 0
                || fromIndex >= toIndex
                || toIndex > items.size()
                || items.isEmpty()) {
            return;
        }
        long start;
        long end;
        if (fromIndex == 0) {
            start = positions.getStartPosition(unit, items.get(0));
        } else if (toIndex == items.size()) {
            start = positions.getEndPosition(unit, items.get(fromIndex - 1));
        } else {
            start = positions.getStartPosition(unit, items.get(fromIndex));
        }
        if (toIndex < items.size()) {
            end = positions.getStartPosition(unit, items.get(toIndex));
        } else {
            end = positions.getEndPosition(unit, items.get(items.size() - 1));
        }
        emitRange(
                path,
                source,
                start,
                end,
                kind,
                label,
                "",
                group,
                role);
    }

    private static void emitTreeReplacement(
            Path path,
            String source,
            CompilationUnitTree unit,
            Tree target,
            Tree replacement,
            String label,
            SourcePositions positions) {
        long start = positions.getStartPosition(unit, target);
        long end = positions.getEndPosition(unit, target);
        long replacementStart = positions.getStartPosition(unit, replacement);
        long replacementEnd = positions.getEndPosition(unit, replacement);
        if (!validRange(source, start, end)
                || !validRange(source, replacementStart, replacementEnd)) {
            return;
        }
        int byteStart = byteOffset(source, start);
        int byteEnd = byteOffset(source, end);
        int replacementByteStart = byteOffset(source, replacementStart);
        int replacementByteEnd = byteOffset(source, replacementEnd);
        OUTPUT.println(
                "{\"path\":\""
                        + escape(path.toString())
                        + "\",\"kind\":\"expression\",\"start\":"
                        + byteStart
                        + ",\"end\":"
                        + byteEnd
                        + ",\"label\":\""
                        + escape(boundedLabel(label))
                        + "\",\"replacement_start\":"
                        + replacementByteStart
                        + ",\"replacement_end\":"
                        + replacementByteEnd
                        + "}");
    }

    private static String sourceOf(
            String source,
            CompilationUnitTree unit,
            Tree tree,
            SourcePositions positions) {
        long start = positions.getStartPosition(unit, tree);
        long end = positions.getEndPosition(unit, tree);
        if (!validRange(source, start, end)) {
            return null;
        }
        return source.substring((int) start, (int) end);
    }

    private static String literalReplacement(LiteralTree literal) {
        Object value = literal.getValue();
        if (value instanceof String) {
            return "\"\"";
        }
        if (value instanceof Number) {
            return "0";
        }
        if (value instanceof Boolean) {
            return "false";
        }
        if (value instanceof Character) {
            return "'\\0'";
        }
        return null;
    }

    private static String memberLabel(Tree member) {
        if (member instanceof MethodTree) {
            return "method:" + ((MethodTree) member).getName();
        }
        if (member instanceof VariableTree) {
            return "field:" + ((VariableTree) member).getName();
        }
        if (member instanceof ClassTree) {
            return "type:" + ((ClassTree) member).getSimpleName();
        }
        return member.getKind().name().toLowerCase(Locale.ROOT);
    }

    private static void emit(
            Path path,
            String source,
            CompilationUnitTree unit,
            Tree tree,
            String kind,
            String label,
            SourcePositions positions) {
        emit(path, source, unit, tree, kind, label, positions, null);
    }

    private static void emit(
            Path path,
            String source,
            CompilationUnitTree unit,
            Tree tree,
            String kind,
            String label,
            SourcePositions positions,
            String replacement) {
        long start = positions.getStartPosition(unit, tree);
        long end = positions.getEndPosition(unit, tree);
        emitRange(path, source, start, end, kind, label, replacement);
    }

    private static void emitRange(
            Path path,
            String source,
            long start,
            long end,
            String kind,
            String label,
            String replacement) {
        emitRange(path, source, start, end, kind, label, replacement, null, null);
    }

    private static void emitRange(
            Path path,
            String source,
            long start,
            long end,
            String kind,
            String label,
            String replacement,
            String group,
            String role) {
        if (!validRange(source, start, end)) {
            return;
        }
        int byteStart = byteOffset(source, start);
        int byteEnd = byteOffset(source, end);
        OUTPUT.println(
                "{\"path\":\""
                        + escape(path.toString())
                        + "\",\"kind\":\""
                        + escape(kind)
                        + "\",\"start\":"
                        + byteStart
                        + ",\"end\":"
                        + byteEnd
                        + ",\"label\":\""
                        + escape(boundedLabel(label))
                        + "\""
                        + (replacement == null
                                ? ""
                                : ",\"replacement\":\"" + escape(replacement) + "\"")
                        + (group == null
                                ? ""
                                : ",\"group\":\""
                                        + escape(group)
                                        + "\",\"role\":\""
                                        + escape(role)
                                        + "\"")
                        + "}");
    }

    private static boolean validRange(String source, long start, long end) {
        return start >= 0 && end > start && end <= source.length();
    }

    private static int byteOffset(String source, long characterOffset) {
        int[] offsets = UTF8_OFFSETS.get(source);
        if (offsets == null) {
            offsets = buildUtf8Offsets(source);
            UTF8_OFFSETS.put(source, offsets);
        }
        return offsets[(int) characterOffset];
    }

    private static int[] buildUtf8Offsets(String source) {
        int[] offsets = new int[source.length() + 1];
        int bytes = 0;
        for (int index = 0; index < source.length(); index++) {
            offsets[index] = bytes;
            char character = source.charAt(index);
            if (character <= 0x7f) {
                bytes += 1;
            } else if (character <= 0x7ff) {
                bytes += 2;
            } else if (Character.isHighSurrogate(character)
                    && index + 1 < source.length()
                    && Character.isLowSurrogate(source.charAt(index + 1))) {
                // Compiler source positions never split a valid surrogate pair.
                offsets[index + 1] = bytes + 1;
                bytes += 4;
                index++;
            } else if (Character.isSurrogate(character)) {
                bytes += 1;
            } else {
                bytes += 3;
            }
        }
        offsets[source.length()] = bytes;
        return offsets;
    }

    private static String boundedLabel(String value) {
        int limit = 160;
        if (value.length() <= limit) {
            return value;
        }
        if (Character.isHighSurrogate(value.charAt(limit - 1))) {
            limit--;
        }
        return value.substring(0, limit) + "...";
    }

    private static String escape(String value) {
        StringBuilder escaped = new StringBuilder(value.length());
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '\\':
                    escaped.append("\\\\");
                    break;
                case '"':
                    escaped.append("\\\"");
                    break;
                case '\n':
                    escaped.append("\\n");
                    break;
                case '\r':
                    escaped.append("\\r");
                    break;
                case '\t':
                    escaped.append("\\t");
                    break;
                default:
                    if (character < 0x20) {
                        escaped.append(String.format("\\u%04x", (int) character));
                    } else {
                        escaped.append(character);
                    }
            }
        }
        return escaped.toString();
    }
}
