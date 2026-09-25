import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import org.eclipse.jgit.diff.Edit;
import org.eclipse.jgit.diff.EditList;
import org.eclipse.jgit.diff.HistogramDiff;
import org.eclipse.jgit.diff.RawText;
import org.eclipse.jgit.diff.RawTextComparator;

/**
 * Reads one case per line, "BASE64(a) TAB BASE64(b)", and writes one JSON line per case:
 * the edits JGit's HistogramDiff returns under WS_IGNORE_CHANGE, and whether the Myers
 * fallback changed them (the same diff with the fallback disabled returns different edits).
 */
public class JgitEdits {
  static String json(EditList edits) {
    StringBuilder out = new StringBuilder("[");
    for (int i = 0; i < edits.size(); i++) {
      Edit e = edits.get(i);
      if (i > 0) out.append(',');
      out.append('[').append(e.getBeginA()).append(',').append(e.getEndA()).append(',')
          .append(e.getBeginB()).append(',').append(e.getEndB()).append(']');
    }
    return out.append(']').toString();
  }

  public static void main(String[] args) throws Exception {
    Base64.Decoder b64 = Base64.getDecoder();
    BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
    String line;
    while ((line = in.readLine()) != null) {
      String[] parts = line.split("\t", -1);
      RawText a = new RawText(b64.decode(parts[0]));
      RawText b = new RawText(b64.decode(parts[1]));
      EditList withFallback = new HistogramDiff().diff(RawTextComparator.WS_IGNORE_CHANGE, a, b);
      HistogramDiff bare = new HistogramDiff();
      bare.setFallbackAlgorithm(null);
      EditList noFallback = bare.diff(RawTextComparator.WS_IGNORE_CHANGE, a, b);
      String edits = json(withFallback);
      System.out.println("{\"edits\":" + edits + ",\"myers_fallback\":"
          + (!edits.equals(json(noFallback))) + "}");
    }
  }
}
