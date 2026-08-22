import { execFileSync } from "node:child_process";
import {
  mkdirSync,
  readFileSync,
  readdirSync,
  realpathSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

process.on("uncaughtException", () => {
  process.stderr.write("frontend-license-export=FAIL\n");
  process.exitCode = 1;
});

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const argumentsList = process.argv.slice(2);
if (argumentsList.length !== 2 || argumentsList[0] !== "--output") {
  throw new Error("Usage: node scripts/export_frontend_licenses.mjs --output <directory>");
}

const outputRoot = resolve(repositoryRoot, argumentsList[1]);
const relativeOutputRoot = relative(repositoryRoot, outputRoot);
if (
  relativeOutputRoot === "" ||
  relativeOutputRoot === ".." ||
  relativeOutputRoot.startsWith(`..${sep}`) ||
  isAbsolute(relativeOutputRoot)
) {
  throw new Error("The license output directory must be a dedicated repository or build path.");
}
if (statOrNull(outputRoot) !== null) {
  throw new Error("The license output directory must not already exist.");
}
const canonicalRepositoryRoot = realpathSync(repositoryRoot);
const canonicalOutputAncestor = realpathSync(nearestExistingAncestor(outputRoot));
if (
  canonicalOutputAncestor !== canonicalRepositoryRoot &&
  !canonicalOutputAncestor.startsWith(`${canonicalRepositoryRoot}${sep}`)
) {
  throw new Error("The license output directory escapes the repository through a link.");
}
const packageStoreRoot = realpathSync(join(repositoryRoot, "node_modules", ".pnpm"));
if (!packageStoreRoot.startsWith(`${canonicalRepositoryRoot}${sep}`)) {
  throw new Error("The frozen pnpm package store escapes the repository through a link.");
}

const corepack = process.platform === "win32" ? (process.env.ComSpec ?? "cmd.exe") : "corepack";
const corepackArguments =
  process.platform === "win32"
    ? ["/d", "/s", "/c", "corepack pnpm licenses list --prod --json"]
    : ["pnpm", "licenses", "list", "--prod", "--json"];
const pnpmOutput = execFileSync(
  corepack,
  corepackArguments,
  {
    cwd: repositoryRoot,
    encoding: "utf8",
    env: {
      ...process.env,
      CI: "true",
      COREPACK_ENABLE_NETWORK: "0",
      npm_config_offline: "true",
    },
    maxBuffer: 16 * 1024 * 1024,
  },
);
const pnpmInventory = JSON.parse(pnpmOutput);
const records = [];
const packageDirectoryOwners = new Map();

for (const [groupLicense, packages] of Object.entries(pnpmInventory)) {
  if (!Array.isArray(packages)) {
    throw new Error("pnpm returned malformed license metadata.");
  }
  for (const packageEntry of packages) {
    const packageName = packageEntry.name;
    const versions = packageEntry.versions;
    const packageLicense = packageEntry.license ?? groupLicense;
    const packagePaths = packageEntry.paths;
    if (
      typeof packageName !== "string" ||
      !Array.isArray(versions) ||
      versions.length === 0 ||
      typeof packageLicense !== "string" ||
      !Array.isArray(packagePaths)
    ) {
      throw new Error("pnpm returned malformed package license metadata.");
    }
    const normalizedPackageLicense = packageLicense.trim();
    if (normalizedPackageLicense === "" || /[\r\n]/u.test(normalizedPackageLicense)) {
      throw new Error("pnpm returned unsafe package license metadata.");
    }
    for (const version of versions) {
      if (typeof version !== "string") {
        throw new Error("pnpm returned a malformed package version.");
      }
      const matchingRoots = [
        ...new Set(
          packagePaths.map((packagePath) => {
            if (typeof packagePath !== "string") {
              throw new Error("pnpm returned a malformed package path.");
            }
            const packageRoot = realpathSync(resolve(repositoryRoot, packagePath));
            if (!packageRoot.startsWith(`${packageStoreRoot}${sep}`)) {
              throw new Error("A pnpm package path escaped the frozen package store.");
            }
            const identity = packageIdentity(packageRoot);
            if (identity.name !== packageName || identity.version !== version) {
              throw new Error("A pnpm package path did not match its declared identity.");
            }
            return packageRoot;
          }),
        ),
      ];
      if (matchingRoots.length === 0) {
        throw new Error(`No installed package root found for ${packageName}@${version}.`);
      }
      const licenseFiles = new Map();
      for (const packageRoot of matchingRoots) {
        for (const licenseFile of findLicenseFiles(packageRoot)) {
          const relativePath = relative(packageRoot, licenseFile).split(sep).join("/");
          const contents = readFileSync(licenseFile);
          const existing = licenseFiles.get(relativePath);
          if (existing !== undefined && !existing.equals(contents)) {
            throw new Error("Duplicate package license files have different contents.");
          }
          licenseFiles.set(relativePath, contents);
        }
      }
      if (licenseFiles.size === 0) {
        throw new Error(`No license material found for ${packageName}@${version}.`);
      }

      const packageDirectory = safePackageDirectory(packageName, version);
      const packageIdentityKey = `${packageName}\0${version}`;
      const directoryOwner = packageDirectoryOwners.get(packageDirectory);
      if (directoryOwner !== undefined && directoryOwner !== packageIdentityKey) {
        throw new Error("Two package identities map to the same license directory.");
      }
      packageDirectoryOwners.set(packageDirectory, packageIdentityKey);
      const materials = [];
      for (const [relativePath, contents] of [...licenseFiles.entries()].sort()) {
        const destination = join(outputRoot, "packages", packageDirectory, ...relativePath.split("/"));
        mkdirSync(dirname(destination), { recursive: true });
        writeFileSync(destination, contents);
        materials.push({
          path: ["packages", packageDirectory, relativePath].join("/"),
        });
      }
      records.push({
        license: normalizedPackageLicense,
        materials,
        name: packageName,
        version,
      });
    }
  }
}

records.sort((left, right) =>
  `${left.name.toLowerCase()}@${left.version}`.localeCompare(
    `${right.name.toLowerCase()}@${right.version}`,
    "en",
  ),
);
mkdirSync(outputRoot, { recursive: true });
writeFileSync(
  join(outputRoot, "DEPENDENCY_LICENSES.json"),
  `${JSON.stringify({ packages: records, schema_version: 1 }, null, 2)}\n`,
  "utf8",
);
const markdownRows = records.map(
  (record) =>
    `| ${record.name.replaceAll("|", "\\|")} | ${record.version} | ${record.license.replaceAll("|", "\\|")} |`,
);
writeFileSync(
  join(outputRoot, "DEPENDENCY_LICENSES.md"),
  [
    "# Bundled Frontend Dependency Licenses",
    "",
    "| Package | Version | License |",
    "| --- | --- | --- |",
    ...markdownRows,
    "",
    "The adjacent packages directory contains bundled license, notice, or copying files.",
    "",
  ].join("\n"),
  "utf8",
);

process.stdout.write(`frontend-license-records=${records.length}\n`);

function statOrNull(path) {
  try {
    return statSync(path);
  } catch (error) {
    if (error?.code === "ENOENT") {
      return null;
    }
    throw error;
  }
}

function nearestExistingAncestor(path) {
  let candidate = dirname(path);
  while (statOrNull(candidate) === null) {
    const parent = dirname(candidate);
    if (parent === candidate) {
      throw new Error("The license output directory has no existing ancestor.");
    }
    candidate = parent;
  }
  return candidate;
}

function packageIdentity(packageRoot) {
  const manifestPath = join(packageRoot, "package.json");
  if (statOrNull(manifestPath) === null) {
    throw new Error("An installed package manifest is missing.");
  }
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  if (typeof manifest.name !== "string" || typeof manifest.version !== "string") {
    throw new Error("An installed package manifest has no identity.");
  }
  return { name: manifest.name, version: manifest.version };
}

function findLicenseFiles(packageRoot) {
  const canonicalRoot = realpathSync(packageRoot);
  const matches = [];
  const pending = [canonicalRoot];
  while (pending.length > 0) {
    const current = pending.pop();
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      if (entry.name === "node_modules" || entry.name === ".git") {
        continue;
      }
      const candidate = join(current, entry.name);
      if (entry.isDirectory()) {
        pending.push(candidate);
        continue;
      }
      if (
        entry.isFile() &&
        /^(license|licence|copying|notice)([._-].*)?$/iu.test(basename(candidate))
      ) {
        const canonicalCandidate = realpathSync(candidate);
        if (!canonicalCandidate.startsWith(`${canonicalRoot}${sep}`)) {
          throw new Error("A package license path escaped its package root.");
        }
        matches.push(canonicalCandidate);
      }
    }
  }
  return matches.sort();
}

function safePackageDirectory(name, version) {
  const value = `${name}@${version}`.replaceAll("/", "__");
  if (isAbsolute(value) || !/^[A-Za-z0-9@._+-]+$/u.test(value)) {
    throw new Error("A package identity cannot be represented safely on disk.");
  }
  return value;
}
