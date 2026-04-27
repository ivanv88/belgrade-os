fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("cargo:rerun-if-changed=../proto/belgrade_os.proto");
    prost_build::compile_protos(
        &["../proto/belgrade_os.proto"],
        &["../proto"],
    )?;
    Ok(())
}
